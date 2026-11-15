"""initial schema: sessions, drivers, laps, partitioned telemetry_resampled, stints

Revision ID: 0001
Revises:
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BIGPK = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("event_slug", sa.String(100), nullable=False),
        sa.Column("session_type", sa.String(4), nullable=False),
        sa.Column("location", sa.String(100), nullable=False, server_default=""),
        sa.Column("corners", sa.JSON, nullable=True),
        sa.Column("n_laps", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("year", "event_slug", "session_type"),
    )
    op.create_index("ix_sessions_year", "sessions", ["year"])

    op.create_table(
        "drivers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Integer,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("number", sa.String(4), nullable=False, server_default=""),
        sa.Column("team", sa.String(100), nullable=True),
        sa.UniqueConstraint("session_id", "code"),
    )

    op.create_table(
        "laps",
        sa.Column("id", BIGPK, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Integer,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("driver", sa.String(8), nullable=False),
        sa.Column("lap_number", sa.Integer, nullable=False),
        sa.Column("lap_time_s", sa.Float, nullable=True),
        sa.Column("s1_s", sa.Float, nullable=True),
        sa.Column("s2_s", sa.Float, nullable=True),
        sa.Column("s3_s", sa.Float, nullable=True),
        sa.Column("stint", sa.Integer, nullable=True),
        sa.Column("compound", sa.String(16), nullable=True),
        sa.Column("tyre_life", sa.Float, nullable=True),
        sa.Column("track_status", sa.String(16), nullable=True),
        sa.Column("is_accurate", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("pit_in", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("pit_out", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Float, nullable=True),
        sa.Column("lap_start_s", sa.Float, nullable=True),
        sa.Column("has_telemetry", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_laps_session_driver_lap", "laps", ["session_id", "driver", "lap_number"], unique=True
    )

    # Parent table is LIST-partitioned by session_id; one partition per session is created
    # by f1tel.db.ensure_partition when a session is loaded. The primary key must include
    # the partition key.
    op.create_table(
        "telemetry_resampled",
        sa.Column(
            "session_id",
            sa.Integer,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("driver", sa.String(8), nullable=False),
        sa.Column("lap_number", sa.Integer, nullable=False),
        sa.Column("distance_m", sa.Float, nullable=False),
        sa.Column("time_s", sa.Float, nullable=False),
        sa.Column("speed", sa.Float(24), nullable=True),
        sa.Column("throttle", sa.Float(24), nullable=True),
        sa.Column("brake", sa.Float(24), nullable=True),
        sa.Column("gear", sa.Integer, nullable=True),
        sa.Column("rpm", sa.Float(24), nullable=True),
        sa.Column("x", sa.Float(24), nullable=True),
        sa.Column("y", sa.Float(24), nullable=True),
        sa.PrimaryKeyConstraint("session_id", "driver", "lap_number", "distance_m"),
        postgresql_partition_by="LIST (session_id)",
    )
    op.create_index(
        "ix_telemetry_session_driver_lap",
        "telemetry_resampled",
        ["session_id", "driver", "lap_number"],
    )

    op.create_table(
        "stints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Integer,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("driver", sa.String(8), nullable=False),
        sa.Column("stint_number", sa.Integer, nullable=False),
        sa.Column("compound", sa.String(16), nullable=True),
        sa.Column("lap_start", sa.Integer, nullable=False),
        sa.Column("lap_end", sa.Integer, nullable=False),
        sa.Column("n_laps", sa.Integer, nullable=False),
        sa.UniqueConstraint("session_id", "driver", "stint_number"),
    )
    op.create_index("ix_stints_session_driver", "stints", ["session_id", "driver"])


def downgrade() -> None:
    op.drop_table("stints")
    op.drop_table("telemetry_resampled")  # also drops its partitions
    op.drop_table("laps")
    op.drop_table("drivers")
    op.drop_table("sessions")
