"""Tests for loading ingested Parquet into the database."""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from f1tel import dbload
from f1tel.db import Base, LapRow, SessionRow, StintRow, TelemetryResampled
from f1tel.dbload import build_stints, find_session_dirs, load_session_dir


def count(engine: Engine, model) -> int:
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(model)).scalar_one()


def test_load_populates_tables(engine: Engine, fixture_session_dir: Path) -> None:
    assert count(engine, SessionRow) == 1
    assert count(engine, LapRow) == 28
    assert count(engine, StintRow) == 4
    n_tel = count(engine, TelemetryResampled)
    assert n_tel > 20 * 350  # ~4 km at 10 m = ~400 rows per lap, 20 laps
    with engine.connect() as c:
        with_tel = c.execute(select(func.count()).where(LapRow.has_telemetry)).scalar_one()
    with Session(engine) as orm:
        sess = orm.scalars(select(SessionRow)).one()
    assert with_tel == 20
    assert sess.session_type == "R" and sess.event == "Fixture Grand Prix"
    assert [c["number"] for c in sess.corners] == [1, 2, 3]


def test_reload_is_idempotent(engine: Engine, fixture_session_dir: Path) -> None:
    before = count(engine, TelemetryResampled)
    n = load_session_dir(engine, fixture_session_dir)
    assert n == before
    assert count(engine, SessionRow) == 1
    assert count(engine, LapRow) == 28
    assert count(engine, TelemetryResampled) == before


def test_failed_load_leaves_no_partial_session(
    engine: Engine, fixture_session_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = itertools.count(1)
    real = dbload._insert_chunk

    def flaky(eng: Engine, rows: list, attempts: int = 4) -> None:
        if next(calls) == 2:
            raise OperationalError("INSERT", {}, RuntimeError("connection lost"))
        real(eng, rows, attempts)

    monkeypatch.setattr(dbload, "_insert_chunk", flaky)
    with pytest.raises(OperationalError):
        load_session_dir(engine, fixture_session_dir, chunk=500)
    assert count(engine, SessionRow) == 0
    assert count(engine, LapRow) == 0
    assert count(engine, TelemetryResampled) == 0


def test_insert_chunk_retries_dropped_connection(
    engine: Engine, fixture_session_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dbload.time, "sleep", lambda _s: None)
    monkeypatch.setattr(engine, "dispose", lambda *a, **k: None)  # keeps in-memory SQLite alive
    row = {
        "session_id": 1, "driver": "AAA", "lap_number": 99, "distance_m": 0.0, "time_s": 0.0,
        "speed": 1.0, "throttle": 0.0, "brake": 0.0, "gear": 1, "rpm": 1.0, "x": 0.0, "y": 0.0,
    }  # fmt: skip
    failures = {"left": 2}
    real_begin = engine.begin

    def begin():
        if failures["left"] > 0:
            failures["left"] -= 1
            raise OperationalError("BEGIN", {}, RuntimeError("server closed the connection"))
        return real_begin()

    monkeypatch.setattr(engine, "begin", begin)
    before = count(engine, TelemetryResampled)
    dbload._insert_chunk(engine, [row])
    assert count(engine, TelemetryResampled) == before + 1
    assert failures["left"] == 0

    failures["left"] = 10
    with pytest.raises(OperationalError):
        dbload._insert_chunk(engine, [row], attempts=3)


def test_resampled_grid_is_10m(engine: Engine) -> None:
    with engine.connect() as c:
        d = [
            r[0]
            for r in c.execute(
                select(TelemetryResampled.distance_m)
                .where(TelemetryResampled.driver == "AAA", TelemetryResampled.lap_number == 3)
                .order_by(TelemetryResampled.distance_m)
            )
        ]
    assert d[0] == 0 and all(b - a == 10 for a, b in itertools.pairwise(d))


def test_build_stints_and_find_dirs(fixture_session_dir: Path) -> None:
    laps = pd.read_parquet(fixture_session_dir / "laps.parquet")
    st = build_stints(laps)
    row = st[(st.driver == "AAA") & (st.stint_number == 1)].iloc[0]
    assert (row.lap_start, row.lap_end, row.n_laps, row.compound) == (1, 9, 9, "MEDIUM")
    assert build_stints(laps.iloc[0:0]).empty
    root = fixture_session_dir.parents[3]
    assert find_session_dirs(root) == [fixture_session_dir]


@pytest.mark.skipif(
    not os.environ.get("F1TEL_TEST_DATABASE_URL"), reason="needs a PostgreSQL test database"
)
def test_postgres_partitioned_load(fixture_session_dir: Path) -> None:  # pragma: no cover
    """Integration test (CI): alembic-style schema, partition per session, idempotent reload."""
    eng = create_engine(os.environ["F1TEL_TEST_DATABASE_URL"])
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    Base.metadata.create_all(eng)
    n1 = load_session_dir(eng, fixture_session_dir)
    n2 = load_session_dir(eng, fixture_session_dir)
    assert n1 == n2 > 0
    with eng.connect() as c:
        parts = c.execute(
            text(
                "SELECT count(*) FROM pg_inherits WHERE inhparent = 'telemetry_resampled'::regclass"
            )
        ).scalar_one()
    assert parts == 1
