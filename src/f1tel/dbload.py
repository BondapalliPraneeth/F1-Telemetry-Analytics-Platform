"""Load ingested Parquet sessions into the database (resampling to the distance grid)."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from f1tel.align import DEFAULT_STEP_M, AlignmentError, resample_frame
from f1tel.db import (
    DriverRow,
    LapRow,
    SessionRow,
    StintRow,
    TelemetryResampled,
    drop_session_data,
    ensure_partition,
)

_TEL_RENAME = {
    "Speed": "speed",
    "Throttle": "throttle",
    "Brake": "brake",
    "nGear": "gear",
    "RPM": "rpm",
    "X": "x",
    "Y": "y",
}
_TEL_COLUMNS = ["distance_m", "time_s", *_TEL_RENAME.values()]
_LAP_COLUMNS = [
    "driver",
    "lap_number",
    "lap_time_s",
    "s1_s",
    "s2_s",
    "s3_s",
    "stint",
    "compound",
    "tyre_life",
    "track_status",
    "is_accurate",
    "pit_in",
    "pit_out",
    "position",
    "lap_start_s",
]


def find_session_dirs(root: Path) -> list[Path]:
    """Return every ingested session directory (those with a ``meta.json``) under ``root``."""
    return sorted(p.parent for p in root.rglob("meta.json"))


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame to a list of dicts with NaN/NA replaced by ``None``."""
    clean = df.astype(object).where(df.notna(), None)
    return clean.to_dict("records")


def build_stints(laps: pd.DataFrame) -> pd.DataFrame:
    """Derive the stints table (one row per driver and stint) from the laps table."""
    valid = laps.dropna(subset=["stint", "lap_number"])
    if valid.empty:
        return pd.DataFrame(
            columns=["driver", "stint_number", "compound", "lap_start", "lap_end", "n_laps"]
        )
    g = valid.groupby(["driver", "stint"], as_index=False).agg(
        compound=("compound", "first"),
        lap_start=("lap_number", "min"),
        lap_end=("lap_number", "max"),
        n_laps=("lap_number", "count"),
    )
    return g.rename(columns={"stint": "stint_number"}).astype(
        {"stint_number": int, "lap_start": int, "lap_end": int, "n_laps": int}
    )


def load_session_dir(
    engine: Engine, sdir: Path, step: float = DEFAULT_STEP_M, *, chunk: int = 10_000
) -> int:
    """Replace one session in the database with the contents of an ingested directory.

    Reads ``meta.json``, ``laps.parquet`` and every per-lap telemetry file, resamples each
    lap to the ``step`` metre grid and bulk-inserts. Metadata goes in one transaction and
    telemetry in short per-chunk transactions (each retried on a dropped connection), because
    long transactions are cut off by some managed databases. If the load fails, the partial
    session is deleted again, so the database never keeps half a session; re-running is
    idempotent (the old copy of the session is replaced).

    Returns:
        Number of resampled telemetry rows written.
    """
    meta = json.loads((sdir / "meta.json").read_text(encoding="utf-8"))
    laps = pd.read_parquet(sdir / "laps.parquet")
    laps = laps.dropna(subset=["lap_number"]).copy()
    laps["lap_number"] = laps["lap_number"].astype(int)
    ingested_at = datetime.fromisoformat(meta["ingested_at"]) if meta.get("ingested_at") else None

    total = 0
    sid: int | None = None
    try:
        sid, tel_frames = _load_metadata(engine, sdir, meta, laps, ingested_at, step)
        buffer: list[dict[str, Any]] = []
        for frame in tel_frames:
            buffer.extend(_records(frame))
            total += len(frame)
            if len(buffer) >= chunk:
                _insert_chunk(engine, buffer)
                buffer = []
        if buffer:
            _insert_chunk(engine, buffer)
    except BaseException:
        if sid is not None:
            with engine.begin() as conn:
                drop_session_data(conn, sid)
        raise
    return total


def _insert_chunk(engine: Engine, rows: list[dict[str, Any]], attempts: int = 4) -> None:
    """Insert one telemetry chunk in its own transaction, retrying dropped connections."""
    for attempt in range(1, attempts + 1):
        try:
            with engine.begin() as conn:
                conn.execute(insert(TelemetryResampled), rows)
            return
        except OperationalError:
            if attempt == attempts:
                raise
            engine.dispose()
            time.sleep(2.0 * attempt)


def _load_metadata(
    engine: Engine,
    sdir: Path,
    meta: dict[str, Any],
    laps: pd.DataFrame,
    ingested_at: datetime | None,
    step: float,
) -> tuple[int, list[pd.DataFrame]]:
    """Replace the session row, drivers, laps and stints; return the session id and telemetry."""
    with engine.begin() as conn:
        old = conn.execute(
            select(SessionRow.id).where(
                SessionRow.year == meta["year"],
                SessionRow.event_slug == meta["event_slug"],
                SessionRow.session_type == meta["session"],
            )
        ).scalar()
        if old is not None:
            drop_session_data(conn, old)
        sid = conn.execute(
            insert(SessionRow)
            .values(
                year=meta["year"],
                round=meta["round"],
                event=meta["event"],
                event_slug=meta["event_slug"],
                session_type=meta["session"],
                location=meta.get("location", ""),
                corners=meta.get("corners", []),
                n_laps=int(meta.get("n_laps", len(laps))),
                ingested_at=ingested_at,
            )
            .returning(SessionRow.id)
        ).scalar_one()
        ensure_partition(conn, sid)

        drivers = (
            laps[["driver", "driver_number", "team"]]
            .drop_duplicates("driver")
            .rename(columns={"driver": "code", "driver_number": "number"})
        )
        drivers["session_id"] = sid
        conn.execute(insert(DriverRow), _records(drivers))

        tel_frames: list[pd.DataFrame] = []
        has_tel: set[tuple[str, int]] = set()
        for row in laps[laps["is_accurate"]].itertuples():
            path = sdir / row.driver / f"lap_{int(row.lap_number)}.parquet"
            if not path.exists():
                continue
            try:
                res = resample_frame(pd.read_parquet(path), step).rename(columns=_TEL_RENAME)
            except AlignmentError:
                continue
            res = res[_TEL_COLUMNS]
            res["gear"] = res["gear"].round().astype("Int64")
            # Raw feeds occasionally report throttle codes above 100 (seen up to 104).
            res["throttle"] = res["throttle"].clip(0, 100)
            res.insert(0, "lap_number", int(row.lap_number))
            res.insert(0, "driver", row.driver)
            res.insert(0, "session_id", sid)
            tel_frames.append(res)
            has_tel.add((row.driver, int(row.lap_number)))

        lap_rows = laps[_LAP_COLUMNS].copy()
        lap_rows["session_id"] = sid
        lap_rows["has_telemetry"] = [
            (d, n) in has_tel
            for d, n in zip(lap_rows["driver"], lap_rows["lap_number"], strict=True)
        ]
        conn.execute(insert(LapRow), _records(lap_rows))

        stints = build_stints(laps)
        if not stints.empty:
            stints["session_id"] = sid
            conn.execute(insert(StintRow), _records(stints))
    return sid, tel_frames
