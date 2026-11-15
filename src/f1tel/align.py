"""Distance-grid resampling and delta-time computation.

A lap is described by cumulative ``distance`` (m) and ``time`` (s) samples. Laps are
anchored at their first valid sample (distance 0, time 0), resampled to a regular
distance grid (default 10 m) by linear interpolation, and compared as::

    delta(d) = time_B(d) - time_A(d)

A positive delta means B is slower than A at distance ``d``. When laps differ in length
the comparison is truncated to the shorter lap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DEFAULT_STEP_M = 10.0
# Channels that are discrete and are rounded after interpolation.
_DISCRETE = {"nGear"}


class AlignmentError(ValueError):
    """Raised when a lap cannot be aligned (for example too few valid samples)."""


@dataclass(frozen=True)
class ResampledLap:
    """A lap on a regular distance grid."""

    distance: np.ndarray
    time: np.ndarray
    step: float
    channels: Mapping[str, np.ndarray] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.distance.size)

    @property
    def lap_time(self) -> float:
        """Cumulative time at the last grid point (<= the true lap time)."""
        return float(self.time[-1])


@dataclass(frozen=True)
class DeltaResult:
    """Delta trace between two laps on their common distance grid."""

    distance: np.ndarray
    delta: np.ndarray


def clean_trace(distance: np.ndarray, time: np.ndarray) -> np.ndarray:
    """Return the indices of samples to keep for a usable ``(distance, time)`` trace.

    Drops NaN samples, samples where distance goes backwards (non-monotonic) or repeats
    (the first occurrence wins), and samples where time goes backwards.

    Raises:
        AlignmentError: if fewer than two samples remain.
    """
    d = np.asarray(distance, dtype=float)
    t = np.asarray(time, dtype=float)
    if d.shape != t.shape:
        raise AlignmentError("distance and time must have the same length")
    idx = np.flatnonzero(np.isfinite(d) & np.isfinite(t))
    if idx.size:
        # keep a sample only if it strictly extends the distance travelled so far
        running_max = np.maximum.accumulate(d[idx])
        idx = idx[np.concatenate(([True], d[idx][1:] > running_max[:-1]))]
        # and only if time does not go backwards
        t_run = np.maximum.accumulate(t[idx])
        idx = idx[np.concatenate(([True], t[idx][1:] >= t_run[:-1]))]
    if idx.size < 2:
        raise AlignmentError("need at least two valid samples to align a lap")
    return idx


def _interp_channel(grid: np.ndarray, d: np.ndarray, values: np.ndarray) -> np.ndarray:
    ok = np.isfinite(values)
    if ok.sum() == 0:
        return np.full(grid.shape, np.nan)
    if ok.sum() == 1:
        return np.full(grid.shape, float(values[ok][0]))
    return np.interp(grid, d[ok], values[ok])


def resample_lap(
    distance: np.ndarray,
    time: np.ndarray,
    channels: Mapping[str, np.ndarray] | None = None,
    step: float = DEFAULT_STEP_M,
) -> ResampledLap:
    """Resample a lap to a regular distance grid with linear interpolation.

    Args:
        distance: cumulative distance in metres.
        time: elapsed time in seconds for each distance sample.
        channels: optional extra per-sample channels (speed, throttle, ...), same length.
        step: grid spacing in metres.

    Returns:
        The lap on the grid ``0, step, 2*step, ...`` up to the lap length, with the
        cumulative time at each grid point.

    Raises:
        AlignmentError: if the trace has fewer than two valid samples or ``step <= 0``.
    """
    if step <= 0:
        raise AlignmentError("step must be positive")
    d_raw = np.asarray(distance, dtype=float)
    idx = clean_trace(d_raw, time)
    d = d_raw[idx] - d_raw[idx][0]
    t = np.asarray(time, dtype=float)[idx]
    t = t - t[0]
    grid = np.arange(0.0, d[-1] + 1e-9, step)
    out: dict[str, np.ndarray] = {}
    for name, values in (channels or {}).items():
        v = np.asarray(values, dtype=float)
        if v.shape != d_raw.shape:
            raise AlignmentError(f"channel {name!r} has the wrong length")
        r = _interp_channel(grid, d, v[idx])
        out[name] = np.rint(r) if name in _DISCRETE else r
    return ResampledLap(distance=grid, time=np.interp(grid, d, t), step=step, channels=out)


def delta_time(a: ResampledLap, b: ResampledLap) -> DeltaResult:
    """Return ``time_B(d) - time_A(d)`` on the common grid of two resampled laps.

    Raises:
        AlignmentError: if the laps use different grid steps.
    """
    if not np.isclose(a.step, b.step):
        raise AlignmentError("laps must be resampled with the same step")
    n = min(len(a), len(b))
    return DeltaResult(distance=a.distance[:n].copy(), delta=b.time[:n] - a.time[:n])


def mini_sectors(lap: ResampledLap, n: int = 20) -> list[tuple[float, float, float]]:
    """Split a lap into ``n`` equal-length segments.

    Returns:
        ``(start_m, end_m, segment_time_s)`` for each segment.
    """
    if n < 1:
        raise AlignmentError("n must be >= 1")
    length = float(lap.distance[-1])
    edges = np.linspace(0.0, length, n + 1)
    times = np.interp(edges, lap.distance, lap.time)
    return [
        (float(edges[i]), float(edges[i + 1]), float(times[i + 1] - times[i])) for i in range(n)
    ]


def resample_frame(df: pd.DataFrame, step: float = DEFAULT_STEP_M) -> pd.DataFrame:
    """Resample a telemetry frame to the distance grid.

    Expects columns ``Distance`` and ``Time_s``; every other numeric column is
    interpolated as a channel. Returns a frame with ``distance_m`` and ``time_s`` first.
    """
    chans = {c: df[c].to_numpy(dtype=float) for c in df.columns if c not in {"Distance", "Time_s"}}
    lap = resample_lap(df["Distance"].to_numpy(), df["Time_s"].to_numpy(), chans, step)
    out = pd.DataFrame({"distance_m": lap.distance, "time_s": lap.time})
    for name, values in lap.channels.items():
        out[name] = values
    return out
