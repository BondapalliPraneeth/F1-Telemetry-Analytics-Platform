"""Cross-check FastF1 data against OpenF1 lap times and Jolpica pit stops."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LapTimeCheck:
    """Agreement between FastF1 and OpenF1 lap durations."""

    compared: int
    within_tolerance: int
    max_abs_diff_s: float | None
    median_abs_diff_s: float | None

    @property
    def agreement(self) -> float | None:
        """Fraction of compared laps within tolerance."""
        return self.within_tolerance / self.compared if self.compared else None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict including the agreement fraction."""
        return {**asdict(self), "agreement": self.agreement}


def compare_lap_times(
    fastf1_laps: pd.DataFrame, openf1_laps: list[dict[str, Any]], tol_s: float = 0.05
) -> LapTimeCheck:
    """Compare lap times by (driver_number, lap_number).

    Args:
        fastf1_laps: normalised laps (``driver_number``, ``lap_number``, ``lap_time_s``).
        openf1_laps: output of :meth:`OpenF1Client.laps`.
        tol_s: absolute tolerance in seconds for counting a lap as agreeing.
    """
    other = {
        (r["driver_number"], r["lap_number"]): r["lap_duration"]
        for r in openf1_laps
        if r["lap_duration"] is not None
    }
    diffs = []
    for row in fastf1_laps.dropna(subset=["lap_time_s", "lap_number"]).itertuples():
        key = (str(row.driver_number), int(row.lap_number))
        if key in other:
            diffs.append(abs(float(row.lap_time_s) - float(other[key])))
    if not diffs:
        return LapTimeCheck(0, 0, None, None)
    arr = np.asarray(diffs)
    return LapTimeCheck(
        compared=int(arr.size),
        within_tolerance=int((arr <= tol_s).sum()),
        max_abs_diff_s=float(arr.max()),
        median_abs_diff_s=float(np.median(arr)),
    )


def compare_pit_stops(
    laps: pd.DataFrame, jolpica_stops: list[dict[str, Any]], id_to_code: dict[str, str]
) -> dict[str, dict[str, int]]:
    """Compare the number of pit stops per driver (FastF1 pit-in laps vs Jolpica stops).

    Returns:
        ``{driver_code: {"fastf1": n, "jolpica": m}}`` for every driver seen in either source.
    """
    ff1 = laps[laps["pit_in"]].groupby("driver").size().to_dict()
    jol: dict[str, int] = {}
    for stop in jolpica_stops:
        code = id_to_code.get(stop["driver_id"], stop["driver_id"])
        jol[code] = jol.get(code, 0) + 1
    return {
        d: {"fastf1": int(ff1.get(d, 0)), "jolpica": int(jol.get(d, 0))}
        for d in sorted(set(ff1) | set(jol))
    }
