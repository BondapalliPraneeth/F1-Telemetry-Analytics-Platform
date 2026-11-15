"""Print the project's resume metrics, measured on this machine from real data.

Usage (from the repo root, venv active)::

    python scripts/measure.py                 # samples, DB rows, coverage
    python scripts/measure.py --cold 2024 Monza Q   # also time cold FastF1 load vs Parquet reload

Anything that cannot be measured (no data ingested, no database, no credentials, no coverage
run yet) prints ``not measured`` and never a guess.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from f1tel.config import get_settings  # noqa: E402

NOT_MEASURED = "not measured"


def count_samples(raw_dir: Path) -> tuple[int, int, int]:
    """Return (sessions, lap files, telemetry samples) under ``raw_dir`` (Parquet metadata only)."""
    import pyarrow.parquet as pq

    sessions = {p.parent for p in raw_dir.rglob("manifest.json")}
    files = list(raw_dir.rglob("lap_*.parquet"))
    samples = sum(pq.ParquetFile(p).metadata.num_rows for p in files)
    return len(sessions), len(files), samples


def time_parquet_reload(session_dir: Path, repeats: int = 5) -> float | None:
    """Median seconds to read every lap Parquet file of one session."""
    files = sorted(session_dir.rglob("lap_*.parquet"))
    if not files:
        return None
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        for f in files:
            pd.read_parquet(f)
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def time_cold_load(year: int, event: str, session: str, scratch: Path) -> float:
    """Seconds to load a session from FastF1 with an empty cache, with every lap's telemetry."""
    from f1tel import loader

    loader.require_credentials()
    loader.configure_cache(scratch)
    ref = loader.resolve_event(year, event)
    t0 = time.perf_counter()
    src = loader.FastF1Source(ref, session)
    laps = src.laps()
    for row in laps[laps["is_accurate"]].itertuples():
        src.telemetry(row.driver, int(row.lap_number))
    return time.perf_counter() - t0


def db_rows(url: str) -> dict[str, int] | None:
    """Row counts of the main tables, or ``None`` if the database is unreachable."""
    from sqlalchemy import create_engine, text

    try:
        eng = create_engine(url, connect_args={"connect_timeout": 3} if "postgresql" in url else {})
        with eng.connect() as c:
            return {
                t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()
                for t in ("sessions", "laps", "telemetry_resampled")
            }
    except Exception:
        return None


def coverage_percent() -> float | None:
    """Total coverage from the last ``pytest --cov`` run (``.coverage`` file), if present."""
    if not (ROOT / ".coverage").exists():
        return None
    out = ROOT / ".coverage.measure.json"
    try:
        subprocess.run(
            [sys.executable, "-m", "coverage", "json", "-o", str(out), "-q"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        return float(json.loads(out.read_text())["totals"]["percent_covered"])
    except Exception:
        return None
    finally:
        out.unlink(missing_ok=True)


def main() -> int:
    """Measure and print the metrics."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--cold", nargs=3, metavar=("YEAR", "EVENT", "SESSION"))
    args = ap.parse_args()
    settings = get_settings()
    raw = settings.raw_dir

    print("== F1 Telemetry Analytics Platform: measured metrics ==")
    n_sessions, n_files, samples = count_samples(raw) if raw.exists() else (0, 0, 0)
    if samples:
        print(
            f"Raw telemetry samples: {samples:,} ({samples / 1e6:.2f} million) "
            f"across {n_sessions} sessions, {n_files} laps"
        )
    else:
        print(f"Raw telemetry samples: {NOT_MEASURED} (no data under {raw}; run `f1tel seed`)")

    rows = db_rows(settings.database_url)
    if rows:
        print("Database rows: " + ", ".join(f"{k}={v:,}" for k, v in rows.items()))
    else:
        print(f"Database rows: {NOT_MEASURED} (database unreachable)")

    if args.cold and samples:
        year, event, code = int(args.cold[0]), args.cold[1], args.cold[2]
        from f1tel.ingest import session_dir
        from f1tel.loader import resolve_event, session_code

        try:
            scratch = ROOT / "data" / "cache" / "measure-cold"
            if scratch.exists():
                import shutil

                shutil.rmtree(scratch)
            cold = time_cold_load(year, event, code, scratch)
            ref = resolve_event(year, event)
            warm = time_parquet_reload(session_dir(raw, ref, session_code(code)))
            if warm:
                print(
                    f"Cold FastF1 load: {cold:.1f} s; Parquet reload (median of 5): {warm:.2f} s; "
                    f"reload is {(1 - warm / cold) * 100:.1f}% faster ({cold / warm:.1f}x)"
                )
            else:
                print(f"Reload speedup: {NOT_MEASURED} (session not ingested)")
        except Exception as exc:
            print(f"Reload speedup: {NOT_MEASURED} ({type(exc).__name__}: {exc})")
    else:
        print(f"Reload speedup: {NOT_MEASURED} (needs ingested data and --cold YEAR EVENT SESSION)")

    cov = coverage_percent()
    print(
        f"Test coverage: {cov:.1f}%"
        if cov is not None
        else f"Test coverage: {NOT_MEASURED} (run `pytest --cov`)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
