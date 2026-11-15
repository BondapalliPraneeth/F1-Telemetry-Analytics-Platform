"""FastF1 session loading with the on-disk cache enabled.

The pure functions (:func:`normalize_laps`, :func:`normalize_telemetry`, :func:`slugify`)
are network-free and unit tested. :class:`FastF1Source` talks to FastF1 and needs the
network plus Formula 1 account credentials (see README); it is exercised by the nightly
smoke test rather than the unit tests.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

LAP_COLUMNS = [
    "driver",
    "driver_number",
    "team",
    "lap_number",
    "lap_time_s",
    "s1_s",
    "s2_s",
    "s3_s",
    "stint",
    "compound",
    "tyre_life",
    "fresh_tyre",
    "track_status",
    "is_accurate",
    "pit_in",
    "pit_out",
    "position",
    "lap_start_s",
]
TELEMETRY_COLUMNS = [
    "Time_s",
    "Distance",
    "Speed",
    "Throttle",
    "Brake",
    "nGear",
    "RPM",
    "DRS",
    "X",
    "Y",
]

SESSION_NAMES = {"R": "Race", "Q": "Qualifying", "S": "Sprint", "SQ": "Sprint Qualifying"}


class MissingCredentialsError(RuntimeError):
    """Raised when FastF1 needs a Formula 1 account and none is configured."""


@dataclass(frozen=True)
class EventRef:
    """A resolved Grand Prix weekend."""

    year: int
    round: int
    name: str
    location: str = ""

    @property
    def slug(self) -> str:
        """Filesystem-safe event name."""
        return slugify(self.name)


class SessionSource(Protocol):
    """What the ingestion pipeline needs from a loaded session."""

    def laps(self) -> pd.DataFrame:
        """All laps, normalised to :data:`LAP_COLUMNS`."""

    def telemetry(self, driver: str, lap_number: int) -> pd.DataFrame:
        """Telemetry for one lap, normalised to :data:`TELEMETRY_COLUMNS`."""

    def weather(self) -> pd.DataFrame | None:
        """Weather samples or ``None``."""

    def corners(self) -> list[dict[str, Any]]:
        """Corner list ``[{number, letter, distance_m}]`` (may be empty)."""


def slugify(text: str) -> str:
    """Return a filesystem-safe slug, e.g. ``"Italian Grand Prix"`` -> ``"Italian_Grand_Prix"``."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_")
    if not slug:
        raise ValueError("cannot slugify an empty name")
    return slug


def session_code(name: str) -> str:
    """Normalise a session name (``"Q"``, ``"qualifying"``, ``"R"``...) to a short code."""
    key = name.strip().upper()
    if key in SESSION_NAMES:
        return key
    for code, full in SESSION_NAMES.items():
        if key == full.upper():
            return code
    raise ValueError(f"unsupported session {name!r}; use one of {sorted(SESSION_NAMES)}")


def _seconds(series: pd.Series) -> pd.Series:
    if pd.api.types.is_timedelta64_dtype(series):
        return series.dt.total_seconds()
    return pd.to_numeric(series, errors="coerce")


def normalize_laps(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert a FastF1 ``Laps`` frame to the project's snake_case lap table.

    Time columns become float seconds; ``pit_in``/``pit_out`` flag laps with a pit-in or
    pit-out timestamp. Missing source columns become NaN/None.
    """
    n = len(raw)

    def col(name: str) -> pd.Series:
        return raw[name] if name in raw.columns else pd.Series([None] * n, index=raw.index)

    out = pd.DataFrame(index=raw.index)
    out["driver"] = col("Driver").astype(str)
    out["driver_number"] = col("DriverNumber").astype(str)
    out["team"] = col("Team")
    out["lap_number"] = pd.to_numeric(col("LapNumber"), errors="coerce").astype("Int64")
    out["lap_time_s"] = _seconds(col("LapTime"))
    out["s1_s"] = _seconds(col("Sector1Time"))
    out["s2_s"] = _seconds(col("Sector2Time"))
    out["s3_s"] = _seconds(col("Sector3Time"))
    out["stint"] = pd.to_numeric(col("Stint"), errors="coerce").astype("Int64")
    out["compound"] = col("Compound")
    out["tyre_life"] = pd.to_numeric(col("TyreLife"), errors="coerce")
    out["fresh_tyre"] = col("FreshTyre")
    out["track_status"] = col("TrackStatus").astype("string")
    out["is_accurate"] = col("IsAccurate").astype("boolean").fillna(False).astype(bool)
    out["pit_in"] = col("PitInTime").notna()
    out["pit_out"] = col("PitOutTime").notna()
    out["position"] = pd.to_numeric(col("Position"), errors="coerce")
    out["lap_start_s"] = _seconds(col("LapStartTime"))
    return out[LAP_COLUMNS].reset_index(drop=True)


def normalize_telemetry(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert FastF1 lap telemetry to :data:`TELEMETRY_COLUMNS` (float32 channels).

    ``Time`` (lap-relative timedelta) becomes ``Time_s``; ``Brake`` becomes 0/1 and
    missing channels become NaN.
    """
    out = pd.DataFrame(index=raw.index)
    out["Time_s"] = _seconds(raw["Time"]) if "Time" in raw else np.nan
    out["Distance"] = pd.to_numeric(raw.get("Distance"), errors="coerce")
    for name in ("Speed", "Throttle", "Brake", "nGear", "RPM", "DRS", "X", "Y"):
        values = raw[name] if name in raw else pd.Series(np.nan, index=raw.index)
        out[name] = pd.to_numeric(values.astype(float), errors="coerce").astype("float32")
    return out[TELEMETRY_COLUMNS].reset_index(drop=True)


def require_credentials() -> None:
    """Fail early with a clear message if no F1 account credentials are configured.

    FastF1 3.9 signs in to a Formula 1 account to download timing data. Credentials are
    read from ``F1_USERNAME`` and ``F1_PASSWORD`` (or an existing stored token).
    """
    if os.environ.get("F1_USERNAME") and os.environ.get("F1_PASSWORD"):
        return
    try:  # a stored token from a previous login is also fine
        from fastf1.internals.f1account import token_manager  # pragma: no cover

        if token_manager.get_stored() is not None:  # pragma: no cover
            return
    except Exception:
        pass
    raise MissingCredentialsError(
        "FastF1 needs a Formula 1 account. Set F1_USERNAME and F1_PASSWORD in your "
        "shell environment (never in the repo); see README 'Data access'."
    )


def configure_cache(cache_dir: Path) -> Path:  # pragma: no cover - touches FastF1 globals
    """Create ``cache_dir`` and enable the FastF1 HTTP/data cache there."""
    import fastf1

    cache_dir.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.configure(cache_dir=str(cache_dir))
    return cache_dir


def resolve_event(year: int, event: str | int) -> EventRef:  # pragma: no cover - network
    """Resolve a round number or (fuzzy) event name to an :class:`EventRef`."""
    import fastf1

    key: str | int = int(event) if str(event).isdigit() else event
    ev = fastf1.get_event(year, key)
    return EventRef(
        year=year,
        round=int(ev["RoundNumber"]),
        name=str(ev["EventName"]),
        location=str(ev.get("Location", "")),
    )


class FastF1Source:  # pragma: no cover - requires network and credentials
    """:class:`SessionSource` backed by a loaded FastF1 session."""

    def __init__(self, ref: EventRef, code: str) -> None:
        import fastf1

        self.ref = ref
        self.code = session_code(code)
        self.session = fastf1.get_session(ref.year, ref.round, self.code)
        self.session.load(laps=True, telemetry=True, weather=True, messages=False)
        self._laps_raw = self.session.laps
        self._index = {
            (str(lap["Driver"]), int(lap["LapNumber"])): lap for _, lap in self._laps_raw.iterlaps()
        }

    def laps(self) -> pd.DataFrame:
        """All laps, normalised."""
        return normalize_laps(pd.DataFrame(self._laps_raw))

    def telemetry(self, driver: str, lap_number: int) -> pd.DataFrame:
        """Merged car + position telemetry for one lap."""
        return normalize_telemetry(pd.DataFrame(self._index[(driver, lap_number)].get_telemetry()))

    def weather(self) -> pd.DataFrame | None:
        """Weather samples (times as seconds)."""
        w = getattr(self.session, "weather_data", None)
        if w is None or len(w) == 0:
            return None
        w = pd.DataFrame(w).copy()
        if "Time" in w:
            w["Time"] = _seconds(w["Time"])
        return w

    def corners(self) -> list[dict[str, Any]]:
        """Corner numbers and distances from the circuit info (best effort)."""
        try:
            corners = self.session.get_circuit_info().corners
        except Exception:
            return []
        return [
            {
                "number": int(r["Number"]),
                "letter": str(r.get("Letter", "") or ""),
                "distance_m": float(r["Distance"]),
            }
            for _, r in corners.iterrows()
        ]
