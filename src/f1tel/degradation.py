"""Tyre degradation: stint cleaning, fuel correction and a linear fit with a CI.

Fuel correction is an **approximation**. A car gets lighter as fuel burns, so lap times
fall by roughly ``fuel_effect_s_per_lap`` seconds for every lap completed, independent of
tyre wear. We add that gain back::

    corrected = lap_time + fuel_effect_s_per_lap * (lap_number - 1)

so that the corrected times approximate a constant-fuel-load lap. The default of 0.03 s
per lap is a rough, commonly quoted figure (it varies by circuit, car and fuel-burn rate)
and is exposed as a parameter rather than treated as a fact. Never read the fitted slope
as a precise tyre-wear number: it also absorbs track evolution, traffic and driver pace.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

DEFAULT_FUEL_EFFECT_S_PER_LAP = 0.03
# FastF1 track status codes: 4 safety car, 5 red flag, 6 VSC deployed, 7 VSC ending.
NEUTRALISED_CODES = frozenset("4567")
MIN_POINTS = 3


class InsufficientDataError(ValueError):
    """Raised when a stint has too few usable laps to fit a regression."""


@dataclass(frozen=True)
class CleaningReport:
    """How many laps were removed from a stint and why."""

    total: int
    pit_laps: int
    safety_car_laps: int
    first_lap: int
    missing_time: int
    kept: int


@dataclass(frozen=True)
class DegradationFit:
    """Linear fit ``y = intercept + slope * x`` with a confidence interval on the slope."""

    slope: float
    intercept: float
    stderr: float
    ci_low: float
    ci_high: float
    r2: float
    n: int
    confidence: float
    resid_std: float
    x_mean: float
    sxx: float
    t_crit: float

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Fitted values at ``x``."""
        return self.intercept + self.slope * np.asarray(x, dtype=float)

    def band(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Fit and confidence band for the *mean* line at ``x``: ``(fit, low, high)``."""
        x = np.asarray(x, dtype=float)
        fit = self.predict(x)
        half = (
            self.t_crit * self.resid_std * np.sqrt(1.0 / self.n + (x - self.x_mean) ** 2 / self.sxx)
        )
        return fit, fit - half, fit + half


def is_neutralised(track_status: str | None) -> bool:
    """True if a lap's track-status string contains a safety-car, VSC or red-flag code."""
    if (
        track_status is None
        or track_status is pd.NA
        or (isinstance(track_status, float) and np.isnan(track_status))
    ):
        return False
    return any(ch in NEUTRALISED_CODES for ch in str(track_status))


def clean_stint(
    laps: pd.DataFrame, *, drop_first_race_lap: bool = True
) -> tuple[pd.DataFrame, CleaningReport]:
    """Remove laps that say nothing about tyre wear.

    Drops in-laps and out-laps (``pit_in``/``pit_out``), safety-car / VSC / red-flag laps,
    laps without a lap time and (optionally) lap 1 of the race (standing start).

    Args:
        laps: columns ``lap_number``, ``lap_time_s``, ``pit_in``, ``pit_out``, ``track_status``.

    Returns:
        The cleaned frame and a :class:`CleaningReport`.
    """
    df = laps.copy()
    pit = df["pit_in"].fillna(False).astype(bool) | df["pit_out"].fillna(False).astype(bool)
    sc = df["track_status"].map(is_neutralised)
    first = (df["lap_number"] == 1) if drop_first_race_lap else pd.Series(False, index=df.index)
    missing = df["lap_time_s"].isna()
    keep = ~(pit | sc | first | missing)
    report = CleaningReport(
        total=len(df),
        pit_laps=int(pit.sum()),
        safety_car_laps=int((sc & ~pit).sum()),
        first_lap=int((first & ~pit & ~sc).sum()),
        missing_time=int((missing & ~pit & ~sc & ~first).sum()),
        kept=int(keep.sum()),
    )
    return df[keep].reset_index(drop=True), report


def fuel_correct(
    laps: pd.DataFrame, fuel_effect_s_per_lap: float = DEFAULT_FUEL_EFFECT_S_PER_LAP
) -> pd.DataFrame:
    """Add ``corrected_s`` = ``lap_time_s + fuel_effect_s_per_lap * (lap_number - 1)``.

    See the module docstring: this is an approximation, and ``fuel_effect_s_per_lap=0``
    disables it.
    """
    df = laps.copy()
    df["corrected_s"] = df["lap_time_s"] + fuel_effect_s_per_lap * (df["lap_number"] - 1)
    return df


def fit_degradation(x: np.ndarray, y: np.ndarray, confidence: float = 0.95) -> DegradationFit:
    """Ordinary least squares of ``y`` on ``x`` with a ``confidence`` interval on the slope.

    Raises:
        InsufficientDataError: with fewer than 3 points or no spread in ``x``.
        ValueError: if ``confidence`` is not in (0, 1).
    """
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = x.size
    if n < MIN_POINTS or np.ptp(x) == 0:
        raise InsufficientDataError(f"need >= {MIN_POINTS} laps with distinct tyre ages (got {n})")
    res = stats.linregress(x, y)
    dof = n - 2
    t_crit = float(stats.t.ppf(0.5 + confidence / 2, dof))
    resid = y - (res.intercept + res.slope * x)
    resid_std = float(np.sqrt(np.sum(resid**2) / dof))
    sxx = float(np.sum((x - x.mean()) ** 2))
    half = t_crit * float(res.stderr)
    return DegradationFit(
        slope=float(res.slope),
        intercept=float(res.intercept),
        stderr=float(res.stderr),
        ci_low=float(res.slope) - half,
        ci_high=float(res.slope) + half,
        r2=float(res.rvalue**2),
        n=n,
        confidence=confidence,
        resid_std=resid_std,
        x_mean=float(x.mean()),
        sxx=sxx,
        t_crit=t_crit,
    )


@dataclass(frozen=True)
class StintAnalysis:
    """Result of :func:`analyze_stint`."""

    points: pd.DataFrame
    cleaning: CleaningReport
    fit: DegradationFit


def tyre_age(laps: pd.DataFrame) -> pd.Series:
    """Tyre age per lap: ``tyre_life`` if known, else the lap index within the stint (from 1)."""
    if "tyre_life" in laps and laps["tyre_life"].notna().all():
        return laps["tyre_life"].astype(float)
    return (laps["lap_number"] - laps["lap_number"].min() + 1).astype(float)


def analyze_stint(
    laps: pd.DataFrame,
    fuel_effect_s_per_lap: float = DEFAULT_FUEL_EFFECT_S_PER_LAP,
    confidence: float = 0.95,
) -> StintAnalysis:
    """Clean, fuel-correct and fit one stint's laps.

    Returns a :class:`StintAnalysis` whose ``points`` has ``tyre_age`` and ``corrected_s``.

    Raises:
        InsufficientDataError: if too few laps survive cleaning.
    """
    if laps.empty:
        raise InsufficientDataError("stint has no laps")
    age = tyre_age(laps)
    base = laps.assign(tyre_age=age)
    cleaned, report = clean_stint(base)
    corrected = fuel_correct(cleaned, fuel_effect_s_per_lap)
    fit = fit_degradation(
        corrected["tyre_age"].to_numpy(), corrected["corrected_s"].to_numpy(), confidence
    )
    return StintAnalysis(points=corrected, cleaning=report, fit=fit)
