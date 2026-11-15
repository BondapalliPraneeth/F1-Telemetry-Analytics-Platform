"""Idempotent ingestion pipeline and the ``f1tel`` command-line interface.

Layout written per session::

    data/raw/{year}/{event}/{session}/
        manifest.json          SHA-256 of every file below, plus a ``complete`` flag
        meta.json  laps.parquet  weather.parquet  results.json  pitstops.json
        {driver}/lap_{n}.parquet     telemetry of accurate laps only

Re-running is safe: files whose checksum matches the manifest are skipped, and a session
marked complete is not even loaded from the network again.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import typer

from f1tel import loader
from f1tel.clients.jolpica import JolpicaClient
from f1tel.config import Settings, get_settings
from f1tel.loader import EventRef, SessionSource, session_code
from f1tel.manifest import Manifest, atomic_write_bytes
from f1tel.retry import NETWORK_ERRORS, RetryableHTTPError, retry_call

log = logging.getLogger("f1tel")

app = typer.Typer(
    help="F1 telemetry platform CLI (unofficial; built on public data).", no_args_is_help=True
)

SEED_SESSIONS: list[tuple[int, str, str]] = [
    (2024, "Monza", "Q"),
    (2024, "Monza", "R"),
    (2024, "Bahrain", "R"),
    (2024, "Monaco", "Q"),
    (2024, "Silverstone", "R"),
]


@dataclass
class IngestReport:
    """Outcome of ingesting one session."""

    session_dir: Path
    written: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    rows: int = 0
    already_complete: bool = False


def session_dir(root: Path, ref: EventRef, code: str) -> Path:
    """Directory for one session: ``root/{year}/{event}/{session}``."""
    return root / str(ref.year) / ref.slug / session_code(code)


def lap_rel_path(driver: str, lap_number: int) -> str:
    """Manifest-relative path of a lap's telemetry file."""
    return f"{driver}/lap_{lap_number}.parquet"


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    """Write ``df`` as zstd Parquet to ``path`` via a temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def _write_json(manifest: Manifest, rel: str, payload: Any) -> None:
    atomic_write_bytes(manifest.root / rel, json.dumps(payload, indent=1, default=str).encode())
    manifest.record(rel)


def ingest_session(
    source: SessionSource,
    ref: EventRef,
    code: str,
    root: Path,
    *,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> IngestReport:
    """Write laps and per-lap telemetry for one loaded session. Safe to re-run.

    Only laps flagged ``is_accurate`` get a telemetry file. Existing files whose checksum
    matches the manifest are skipped unless ``force`` is set. The manifest is saved even if
    an error interrupts the run, so a retry resumes where it stopped.
    """
    code = session_code(code)
    sdir = session_dir(root, ref, code)
    manifest = Manifest.load(sdir)
    report = IngestReport(session_dir=sdir)
    say = progress or (lambda _msg: None)

    laps = source.laps()
    accurate = laps[laps["is_accurate"] & laps["lap_number"].notna()]
    manifest.complete = False
    try:
        laps_path = sdir / "laps.parquet"
        write_parquet_atomic(laps, laps_path)
        manifest.record("laps.parquet", rows=len(laps))
        weather = source.weather()
        if weather is not None:
            write_parquet_atomic(weather, sdir / "weather.parquet")
            manifest.record("weather.parquet", rows=len(weather))
        meta = {
            "year": ref.year,
            "round": ref.round,
            "event": ref.name,
            "event_slug": ref.slug,
            "location": ref.location,
            "session": code,
            "n_laps": len(laps),
            "n_accurate": len(accurate),
            "corners": source.corners(),
            "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _write_json(manifest, "meta.json", meta)

        total = len(accurate)
        for i, row in enumerate(accurate.itertuples(), start=1):
            rel = lap_rel_path(row.driver, int(row.lap_number))
            if not force and manifest.is_intact(rel):
                report.skipped += 1
                continue
            try:
                tel = source.telemetry(row.driver, int(row.lap_number))
            except (KeyError, ValueError, IndexError) as exc:
                report.failed.append(f"{rel}: {exc!r}")
                continue
            if tel.empty:
                report.failed.append(f"{rel}: empty telemetry")
                continue
            write_parquet_atomic(tel, sdir / rel)
            manifest.record(rel, rows=len(tel))
            report.written += 1
            report.rows += len(tel)
            if i % 50 == 0:
                say(f"  {i}/{total} laps")
        manifest.complete = not report.failed
    finally:
        manifest.save()
    return report


def fetch_external(
    ref: EventRef, code: str, sdir: Path, settings: Settings, *, client: JolpicaClient | None = None
) -> None:
    """Best-effort: save Jolpica results and pit stops for a race next to the telemetry."""
    if session_code(code) not in {"R", "S"}:
        return
    own = client is None
    client = client or JolpicaClient(
        settings.jolpica_base_url,
        cache_dir=settings.data_dir / "cache" / "jolpica",
        timeout_s=settings.http_timeout_s,
        retries=settings.http_max_retries,
    )
    manifest = Manifest.load(sdir)
    try:
        _write_json(manifest, "results.json", client.results(ref.year, ref.round))
        _write_json(manifest, "pitstops.json", client.pit_stops(ref.year, ref.round))
    except (httpx.HTTPError, RetryableHTTPError, KeyError, ValueError) as exc:
        log.warning("Jolpica data unavailable for %s %s: %r", ref.year, ref.name, exc)
    finally:
        manifest.save()
        if own:
            client.close()


def ingest_one(
    year: int,
    event: str,
    session: str,
    settings: Settings | None = None,
    *,
    force: bool = False,
    resolver: Callable[[int, str], EventRef] | None = None,
    source_factory: Callable[[EventRef, str], SessionSource] | None = None,
    progress: Callable[[str], None] | None = None,
) -> IngestReport:
    """Resolve, load and ingest one session, skipping it if already complete."""
    settings = settings or get_settings()
    code = session_code(session)
    ref = (resolver or loader.resolve_event)(year, event)  # type: ignore[arg-type]
    sdir = session_dir(settings.raw_dir, ref, code)
    manifest = Manifest.load(sdir)
    if manifest.complete and not force and not manifest.verify():
        return IngestReport(session_dir=sdir, already_complete=True, skipped=len(manifest.files))

    factory = source_factory or loader.FastF1Source
    source = retry_call(
        lambda: factory(ref, code), retries=settings.http_max_retries, exceptions=NETWORK_ERRORS
    )
    report = ingest_session(source, ref, code, settings.raw_dir, force=force, progress=progress)
    fetch_external(ref, code, sdir, settings)
    return report


def parse_int_list(spec: str) -> list[int]:
    """Parse ``"1-4,7"`` into ``[1, 2, 3, 4, 7]``."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def batch_plan(
    years: Iterable[int], rounds: Iterable[int], sessions: Iterable[str]
) -> list[tuple[int, str, str]]:
    """Cross product of years x rounds x sessions as ``(year, round_as_str, session)``."""
    rounds = list(rounds)
    sess = [session_code(s) for s in sessions]
    return [(y, str(r), s) for y in years for r in rounds for s in sess]


def _setup(settings: Settings, data_dir: Path | None) -> Settings:
    if data_dir is not None:
        settings = settings.model_copy(update={"data_dir": data_dir})
    loader.require_credentials()
    loader.configure_cache(settings.fastf1_cache_dir)
    return settings


def _run_many(
    plan: list[tuple[int, str, str]], settings: Settings, force: bool, to_db: bool
) -> int:
    failures = 0
    for n, (year, event, sess) in enumerate(plan, start=1):
        label = f"[{n}/{len(plan)}] {year} {event} {sess}"
        try:
            rep = ingest_one(year, event, sess, settings, force=force, progress=typer.echo)
        except Exception as exc:
            failures += 1
            typer.secho(f"{label}: FAILED {exc!r}", fg=typer.colors.RED)
            continue
        if rep.already_complete:
            typer.echo(f"{label}: already complete, skipped")
        else:
            typer.echo(
                f"{label}: wrote {rep.written}, skipped {rep.skipped}, failed {len(rep.failed)}"
            )
            failures += bool(rep.failed)
        if to_db:
            from f1tel.db import get_engine
            from f1tel.dbload import load_session_dir

            load_session_dir(get_engine(settings.database_url), rep.session_dir)
    return failures


@app.command()
def ingest(
    year: int = typer.Option(..., help="Season, e.g. 2024"),
    event: str = typer.Option(..., help="Event name or round number, e.g. Monza or 16"),
    session: str = typer.Option("R", help="R, Q, S or SQ"),
    force: bool = typer.Option(False, help="Re-write files even if their checksum matches"),
    to_db: bool = typer.Option(False, "--to-db", help="Also load into PostgreSQL"),
    data_dir: Path | None = typer.Option(None, help="Override F1TEL_DATA_DIR"),
) -> None:
    """Ingest one session into the Parquet cache (idempotent)."""
    settings = _setup(get_settings(), data_dir)
    raise typer.Exit(_run_many([(year, event, session)], settings, force, to_db))


@app.command()
def batch(
    years: str = typer.Option("2023,2024,2025", help="Comma list or ranges, e.g. 2023-2025"),
    rounds: str = typer.Option("1-8", help="Round numbers, e.g. 1-8,10"),
    sessions: str = typer.Option("R", help="Comma list of session codes"),
    force: bool = typer.Option(False),
    to_db: bool = typer.Option(False, "--to-db"),
    data_dir: Path | None = typer.Option(None),
) -> None:
    """Ingest many sessions (default: rounds 1-8 of 2023-2025 races = 24 sessions)."""
    settings = _setup(get_settings(), data_dir)
    plan = batch_plan(parse_int_list(years), parse_int_list(rounds), sessions.split(","))
    typer.echo(f"{len(plan)} sessions planned")
    failures = _run_many(plan, settings, force, to_db)
    typer.echo(f"done, {failures} session(s) with failures")
    raise typer.Exit(1 if failures else 0)


@app.command()
def seed(
    data_dir: Path | None = typer.Option(None),
    skip_db: bool = typer.Option(False, "--skip-db", help="Only fill the Parquet cache"),
) -> None:
    """Ingest the 5 demo sessions (including Monza 2024 Q) and load them into PostgreSQL."""
    settings = _setup(get_settings(), data_dir)
    plan = [(y, e, s) for y, e, s in SEED_SESSIONS]
    failures = _run_many(plan, settings, False, not skip_db)
    raise typer.Exit(1 if failures else 0)


@app.command("load-db")
def load_db(
    session_path: Path | None = typer.Option(None, help="One session directory"),
    data_dir: Path | None = typer.Option(None),
) -> None:
    """Load already-ingested Parquet sessions into PostgreSQL (all, or one directory)."""
    from f1tel.db import get_engine
    from f1tel.dbload import find_session_dirs, load_session_dir

    settings = get_settings()
    root = (data_dir or settings.data_dir) / "raw"
    engine = get_engine(settings.database_url)
    dirs = [session_path] if session_path else find_session_dirs(root)
    for d in dirs:
        n = load_session_dir(engine, d, step=settings.grid_step_m)
        typer.echo(f"{d}: {n} resampled rows")
    if not dirs:
        typer.echo("nothing to load")


@app.command()
def verify(data_dir: Path | None = typer.Option(None)) -> None:
    """Re-check every manifest checksum under the data directory."""
    root = (data_dir or get_settings().data_dir) / "raw"
    bad = 0
    for mpath in sorted(root.rglob("manifest.json")):
        problems = Manifest.load(mpath.parent).verify()
        bad += len(problems)
        status = "ok" if not problems else f"{len(problems)} bad"
        typer.echo(f"{mpath.parent.relative_to(root)}: {status}")
    raise typer.Exit(1 if bad else 0)


@app.command()
def crosscheck(
    year: int = typer.Option(...),
    event: str = typer.Option(...),
    session: str = typer.Option("R"),
    tol: float = typer.Option(0.05, help="Lap-time tolerance in seconds"),
) -> None:
    """Compare an ingested session against OpenF1 lap times and Jolpica pit stops."""
    from f1tel.clients.openf1 import OpenF1Client
    from f1tel.crosscheck import compare_lap_times, compare_pit_stops

    settings = get_settings()
    ref = loader.resolve_event(year, event)
    code = session_code(session)
    sdir = session_dir(settings.raw_dir, ref, code)
    laps = pd.read_parquet(sdir / "laps.parquet")
    client = OpenF1Client(
        settings.openf1_base_url,
        cache_dir=settings.data_dir / "cache" / "openf1",
        timeout_s=settings.http_timeout_s,
        retries=settings.http_max_retries,
    )
    try:
        key = client.find_session_key(year, ref.location, code)
        if key is None:
            typer.echo("OpenF1 has no matching session")
            raise typer.Exit(1)
        check = compare_lap_times(laps, client.laps(key), tol)
    finally:
        client.close()
    typer.echo(json.dumps(check.to_dict(), indent=1))
    pits = sdir / "pitstops.json"
    results = sdir / "results.json"
    if pits.exists() and results.exists():
        id_to_code = {r["driver_id"]: r["code"] for r in json.loads(results.read_text())}
        typer.echo(json.dumps(compare_pit_stops(laps, json.loads(pits.read_text()), id_to_code)))


if __name__ == "__main__":  # pragma: no cover
    app()
