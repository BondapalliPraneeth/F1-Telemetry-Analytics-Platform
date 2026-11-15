"""SQLAlchemy 2.x models and engine helpers.

``telemetry_resampled`` is declared as a PostgreSQL ``LIST`` partitioned table on
``session_id``; :func:`ensure_partition` creates one partition per session. On other
dialects (SQLite in tests) the partitioning options are ignored and it is a plain table.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

BigIntPK = BigInteger().with_variant(Integer, "sqlite")
Real = Float(24)  # REAL (4 bytes) on PostgreSQL


class Base(DeclarativeBase):
    """Declarative base."""


class SessionRow(Base):
    """One ingested session (e.g. Monza 2024 Qualifying)."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("year", "event_slug", "session_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    round: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(100))
    event_slug: Mapped[str] = mapped_column(String(100))
    session_type: Mapped[str] = mapped_column(String(4))
    location: Mapped[str] = mapped_column(String(100), default="")
    corners: Mapped[Any] = mapped_column(JSON, default=list)
    n_laps: Mapped[int] = mapped_column(Integer, default=0)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DriverRow(Base):
    """A driver in a session."""

    __tablename__ = "drivers"
    __table_args__ = (UniqueConstraint("session_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(8))
    number: Mapped[str] = mapped_column(String(4), default="")
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)


class LapRow(Base):
    """One lap of one driver."""

    __tablename__ = "laps"
    __table_args__ = (
        Index("ix_laps_session_driver_lap", "session_id", "driver", "lap_number", unique=True),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    driver: Mapped[str] = mapped_column(String(8))
    lap_number: Mapped[int] = mapped_column(Integer)
    lap_time_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    s1_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    s2_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    s3_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    stint: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compound: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tyre_life: Mapped[float | None] = mapped_column(Float, nullable=True)
    track_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_accurate: Mapped[bool] = mapped_column(Boolean, default=False)
    pit_in: Mapped[bool] = mapped_column(Boolean, default=False)
    pit_out: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    lap_start_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_telemetry: Mapped[bool] = mapped_column(Boolean, default=False)


class TelemetryResampled(Base):
    """Telemetry on the 10 m distance grid. Partitioned by ``session_id`` on PostgreSQL."""

    __tablename__ = "telemetry_resampled"
    __table_args__ = (
        Index("ix_telemetry_session_driver_lap", "session_id", "driver", "lap_number"),
        {"postgresql_partition_by": "LIST (session_id)"},
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    driver: Mapped[str] = mapped_column(String(8), primary_key=True)
    lap_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    distance_m: Mapped[float] = mapped_column(Float, primary_key=True)
    time_s: Mapped[float] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Real, nullable=True)
    throttle: Mapped[float | None] = mapped_column(Real, nullable=True)
    brake: Mapped[float | None] = mapped_column(Real, nullable=True)
    gear: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpm: Mapped[float | None] = mapped_column(Real, nullable=True)
    x: Mapped[float | None] = mapped_column(Real, nullable=True)
    y: Mapped[float | None] = mapped_column(Real, nullable=True)


class StintRow(Base):
    """A tyre stint derived from the laps table."""

    __tablename__ = "stints"
    __table_args__ = (
        UniqueConstraint("session_id", "driver", "stint_number"),
        Index("ix_stints_session_driver", "session_id", "driver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    driver: Mapped[str] = mapped_column(String(8))
    stint_number: Mapped[int] = mapped_column(Integer)
    compound: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lap_start: Mapped[int] = mapped_column(Integer)
    lap_end: Mapped[int] = mapped_column(Integer)
    n_laps: Mapped[int] = mapped_column(Integer)


@lru_cache
def get_engine(url: str) -> Engine:
    """Return a cached engine for ``url``."""
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    """Return a ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(engine, expire_on_commit=False)


def _partition_name(session_id: int) -> str:
    return f"telemetry_resampled_s{int(session_id)}"


def ensure_partition(conn: Connection, session_id: int) -> None:
    """Create the telemetry partition for ``session_id`` (PostgreSQL only)."""
    if conn.dialect.name != "postgresql":
        return
    sid = int(session_id)
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_partition_name(sid)} "
            f"PARTITION OF telemetry_resampled FOR VALUES IN ({sid})"
        )
    )


def drop_session_data(conn: Connection, session_id: int) -> None:
    """Delete a session and all its rows (dropping the telemetry partition on PostgreSQL)."""
    sid = int(session_id)
    if conn.dialect.name == "postgresql":
        conn.execute(text(f"DROP TABLE IF EXISTS {_partition_name(sid)}"))
    else:
        conn.execute(
            TelemetryResampled.__table__.delete().where(TelemetryResampled.session_id == sid)
        )
    for model in (StintRow, LapRow, DriverRow):
        conn.execute(model.__table__.delete().where(model.session_id == sid))
    conn.execute(SessionRow.__table__.delete().where(SessionRow.id == sid))
