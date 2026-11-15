"""Pydantic response models for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    """Liveness/readiness response."""

    status: str
    database: str
    version: str


class SessionOut(BaseModel):
    """An ingested session."""

    id: int
    year: int
    round: int
    event: str
    session_type: str
    location: str
    n_laps: int


class DriverOut(BaseModel):
    """A driver in a session."""

    code: str
    number: str
    team: str | None


class LapOut(BaseModel):
    """One lap."""

    driver: str
    lap_number: int
    lap_time_s: float | None
    s1_s: float | None
    s2_s: float | None
    s3_s: float | None
    stint: int | None
    compound: str | None
    tyre_life: float | None
    track_status: str | None
    is_accurate: bool
    pit_in: bool
    pit_out: bool
    has_telemetry: bool


class StintOut(BaseModel):
    """A tyre stint."""

    driver: str
    stint_number: int
    compound: str | None
    lap_start: int
    lap_end: int
    n_laps: int


class CornerOut(BaseModel):
    """A corner marker on the distance axis."""

    number: int
    letter: str = ""
    distance_m: float


class TraceOut(BaseModel):
    """Channels for one lap on the common distance grid."""

    driver: str
    lap_number: int
    lap_time_s: float | None
    time_s: list[float | None]
    speed: list[float | None]
    throttle: list[float | None]
    brake: list[float | None]
    gear: list[float | None]


class SectorOut(BaseModel):
    """Official sector times of both laps."""

    name: str
    a_s: float | None
    b_s: float | None
    delta_s: float | None = Field(description="b - a; positive means B is slower")


class MiniSectorOut(BaseModel):
    """Time over an equal-length slice of the lap."""

    index: int
    start_m: float
    end_m: float
    a_s: float
    b_s: float
    delta_s: float = Field(description="b - a; positive means B is slower")


class CompareOut(BaseModel):
    """Lap comparison: ``delta[i] = time_B - time_A`` at ``distance[i]``."""

    session_id: int
    step_m: float
    distance: list[float]
    delta: list[float | None]
    a: TraceOut
    b: TraceOut
    corners: list[CornerOut]
    sectors: list[SectorOut]
    mini_sectors: list[MiniSectorOut]
    final_delta_s: float | None


class DegradationPoint(BaseModel):
    """One lap used in the regression."""

    lap_number: int
    tyre_age: float
    lap_time_s: float
    corrected_s: float


class FitOut(BaseModel):
    """Regression result for the slope (seconds per lap of tyre age)."""

    slope: float
    intercept: float
    stderr: float
    ci_low: float
    ci_high: float
    r2: float
    n: int
    confidence: float


class BandPoint(BaseModel):
    """Fitted line with its confidence band at one tyre age."""

    tyre_age: float
    fit: float
    low: float
    high: float


class CleaningOut(BaseModel):
    """Laps removed before fitting."""

    total: int
    pit_laps: int
    safety_car_laps: int
    first_lap: int
    missing_time: int
    kept: int


class DegradationOut(BaseModel):
    """Tyre degradation for one stint."""

    session_id: int
    driver: str
    stint: int
    compound: str | None
    fuel_effect_s_per_lap: float
    note: str
    points: list[DegradationPoint]
    fit: FitOut
    band: list[BandPoint]
    cleaning: CleaningOut
