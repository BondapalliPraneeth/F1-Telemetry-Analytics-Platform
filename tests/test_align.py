"""Tests for distance alignment and delta time."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from f1tel.align import (
    AlignmentError,
    clean_trace,
    delta_time,
    mini_sectors,
    resample_frame,
    resample_lap,
)


def constant_speed_lap(
    speed: float, length: float = 5000.0, n: int = 501
) -> tuple[np.ndarray, np.ndarray]:
    d = np.linspace(0.0, length, n)
    return d, d / speed


@st.composite
def laps(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray]:
    """Random plausible laps: positive step lengths and positive dt."""
    n = draw(st.integers(min_value=5, max_value=60))
    steps = draw(st.lists(st.floats(1.0, 80.0), min_size=n, max_size=n))
    dts = draw(st.lists(st.floats(0.01, 2.0), min_size=n, max_size=n))
    return np.concatenate(([0.0], np.cumsum(steps))), np.concatenate(([0.0], np.cumsum(dts)))


def test_known_constant_speed_time() -> None:
    d, t = constant_speed_lap(50.0)
    lap = resample_lap(d, t, step=10.0)
    assert lap.distance[0] == 0
    assert lap.distance[-1] == 5000
    np.testing.assert_allclose(lap.time, lap.distance / 50.0)


def test_known_delta_between_constant_speeds() -> None:
    a = resample_lap(*constant_speed_lap(50.0))
    b = resample_lap(*constant_speed_lap(60.0))
    res = delta_time(a, b)
    np.testing.assert_allclose(res.delta, res.distance / 60.0 - res.distance / 50.0)
    assert res.delta[-1] < 0  # B is faster


def test_delta_of_lap_with_itself_is_zero() -> None:
    lap = resample_lap(*constant_speed_lap(55.0))
    assert np.all(delta_time(lap, lap).delta == 0)


@settings(max_examples=60, deadline=None)
@given(laps())
def test_property_delta_self_zero(lap_data: tuple[np.ndarray, np.ndarray]) -> None:
    lap = resample_lap(*lap_data)
    np.testing.assert_array_equal(delta_time(lap, lap).delta, 0.0)


@settings(max_examples=60, deadline=None)
@given(laps(), laps())
def test_property_delta_antisymmetric(
    a_data: tuple[np.ndarray, np.ndarray], b_data: tuple[np.ndarray, np.ndarray]
) -> None:
    a, b = resample_lap(*a_data), resample_lap(*b_data)
    ab, ba = delta_time(a, b), delta_time(b, a)
    np.testing.assert_allclose(ab.delta, -ba.delta, atol=1e-12)
    np.testing.assert_array_equal(ab.distance, ba.distance)


def test_different_lengths_truncate_to_shorter() -> None:
    a = resample_lap(*constant_speed_lap(50.0, length=5000.0))
    b = resample_lap(*constant_speed_lap(50.0, length=4000.0, n=401))
    res = delta_time(a, b)
    assert res.distance[-1] == 4000
    np.testing.assert_allclose(res.delta, 0.0, atol=1e-9)


def test_repeated_and_nonmonotonic_distance() -> None:
    d = np.array([0, 10, 10, 20, 15, 30, 40.0])
    t = np.array([0, 1, 1.1, 2, 2.2, 3, 4.0])
    idx = clean_trace(d, t)
    assert d[idx].tolist() == [0, 10, 20, 30, 40]
    lap = resample_lap(d, t, step=10)
    np.testing.assert_allclose(lap.time, [0, 1, 2, 3, 4])


def test_nan_samples_dropped() -> None:
    d = np.array([0, 10, np.nan, 30, 40.0])
    t = np.array([0, 1, 2, np.nan, 4.0])
    lap = resample_lap(d, t, step=10)
    assert not np.isnan(lap.time).any()
    # samples at d=0,10,40 survive; time is linear between them
    np.testing.assert_allclose(lap.time, [0, 1, 2, 3, 4])


def test_time_going_backwards_is_dropped() -> None:
    d = np.array([0, 10, 20, 30.0])
    t = np.array([0, 1, 0.5, 3.0])
    assert d[clean_trace(d, t)].tolist() == [0, 10, 30]


def test_too_few_samples_raises() -> None:
    with pytest.raises(AlignmentError):
        resample_lap(np.array([0.0, np.nan]), np.array([0.0, 1.0]))
    with pytest.raises(AlignmentError):
        resample_lap(np.array([0.0, 1.0]), np.array([0.0]))


def test_bad_step_and_mismatched_step() -> None:
    d, t = constant_speed_lap(50.0)
    with pytest.raises(AlignmentError):
        resample_lap(d, t, step=0)
    with pytest.raises(AlignmentError):
        delta_time(resample_lap(d, t, step=10), resample_lap(d, t, step=20))


def test_origin_is_first_sample() -> None:
    lap = resample_lap(np.array([100.0, 110.0, 120.0]), np.array([5.0, 6.0, 7.0]), step=10)
    assert lap.distance.tolist() == [0, 10, 20]
    assert lap.time.tolist() == [0, 1, 2]


def test_channels_interpolated_and_gear_rounded() -> None:
    d = np.array([0, 10, 20, 30.0])
    t = np.array([0, 1, 2, 3.0])
    ch = {"Speed": np.array([100, 200, 300, 400.0]), "nGear": np.array([2, 3, 4, 5.0])}
    lap = resample_lap(d, t, ch, step=5)
    assert lap.channels["Speed"][1] == pytest.approx(150)
    assert np.all(lap.channels["nGear"] == np.rint(lap.channels["nGear"]))
    with pytest.raises(AlignmentError):
        resample_lap(d, t, {"Speed": np.array([1.0])})


def test_all_nan_and_single_value_channels() -> None:
    d = np.array([0, 10, 20.0])
    t = np.array([0, 1, 2.0])
    ch = {"X": np.array([np.nan] * 3), "Y": np.array([np.nan, 7.0, np.nan])}
    lap = resample_lap(d, t, ch, step=10)
    assert np.isnan(lap.channels["X"]).all()
    assert (lap.channels["Y"] == 7.0).all()


def test_mini_sectors_sum_to_lap_time() -> None:
    lap = resample_lap(*constant_speed_lap(50.0))
    segs = mini_sectors(lap, 20)
    assert len(segs) == 20
    assert sum(s[2] for s in segs) == pytest.approx(lap.lap_time)
    with pytest.raises(AlignmentError):
        mini_sectors(lap, 0)


def test_resample_frame_columns() -> None:
    d, t = constant_speed_lap(50.0, length=100.0, n=11)
    df = pd.DataFrame({"Distance": d, "Time_s": t, "Speed": np.full(11, 180.0)})
    out = resample_frame(df, step=10)
    assert list(out.columns) == ["distance_m", "time_s", "Speed"]
    assert len(out) == 11
    assert out["time_s"].iloc[-1] == pytest.approx(2.0)
