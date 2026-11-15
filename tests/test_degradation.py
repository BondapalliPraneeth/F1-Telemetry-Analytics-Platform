"""Tests for stint cleaning, fuel correction and the degradation fit."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from f1tel.degradation import (
    InsufficientDataError,
    analyze_stint,
    clean_stint,
    fit_degradation,
    fuel_correct,
    is_neutralised,
    tyre_age,
)

TRUE_SLOPE = 0.05
FUEL = 0.03


def synthetic_stint(
    n: int = 15, start_lap: int = 10, noise: float = 0.0, seed: int = 1
) -> pd.DataFrame:
    """Laps whose time = base + tyre wear - fuel burn (+ noise)."""
    rng = np.random.default_rng(seed)
    lap = np.arange(start_lap, start_lap + n)
    age = np.arange(1, n + 1)
    t = 90.0 + TRUE_SLOPE * age - FUEL * (lap - 1) + rng.normal(0, noise, n)
    return pd.DataFrame(
        {
            "lap_number": lap,
            "lap_time_s": t,
            "pit_in": False,
            "pit_out": False,
            "track_status": "1",
            "tyre_life": age.astype(float),
        }
    )


def test_noise_free_recovers_slope_after_fuel_correction() -> None:
    res = analyze_stint(synthetic_stint(), fuel_effect_s_per_lap=FUEL)
    assert res.fit.slope == pytest.approx(TRUE_SLOPE, abs=1e-9)
    assert res.fit.r2 == pytest.approx(1.0)
    assert res.fit.ci_low <= TRUE_SLOPE <= res.fit.ci_high


def test_without_fuel_correction_slope_is_biased() -> None:
    res = analyze_stint(synthetic_stint(), fuel_effect_s_per_lap=0.0)
    assert res.fit.slope == pytest.approx(TRUE_SLOPE - FUEL, abs=1e-9)


def test_noisy_stint_ci_contains_truth_and_is_ordered() -> None:
    res = analyze_stint(synthetic_stint(n=25, noise=0.15), fuel_effect_s_per_lap=FUEL)
    f = res.fit
    assert f.ci_low < f.slope < f.ci_high
    assert f.ci_low <= TRUE_SLOPE <= f.ci_high
    assert f.n == 25


def test_ci_coverage_is_close_to_nominal() -> None:
    hits = 0
    trials = 300
    for seed in range(trials):
        f = analyze_stint(synthetic_stint(n=20, noise=0.2, seed=seed), FUEL).fit
        hits += f.ci_low <= TRUE_SLOPE <= f.ci_high
    assert 0.90 <= hits / trials <= 0.99  # nominal 95%


def test_cleaning_removes_pit_sc_first_and_missing() -> None:
    df = synthetic_stint(n=10, start_lap=1)
    df.loc[0, "pit_out"] = True  # lap 1 is also an out lap
    df.loc[3, "track_status"] = "14"  # safety car during lap
    df.loc[4, "track_status"] = "6"  # VSC
    df.loc[5, "track_status"] = "2"  # yellow only -> kept
    df.loc[6, "lap_time_s"] = np.nan
    df.loc[9, "pit_in"] = True
    cleaned, rep = clean_stint(df)
    assert rep.total == 10
    assert rep.pit_laps == 2
    assert rep.safety_car_laps == 2
    assert rep.missing_time == 1
    assert rep.kept == len(cleaned) == 5
    assert 5 in set(cleaned["lap_number"]) or 6 in set(cleaned["lap_number"])


def test_first_lap_dropped_only_when_requested() -> None:
    df = synthetic_stint(n=5, start_lap=1)
    assert len(clean_stint(df)[0]) == 4
    assert len(clean_stint(df, drop_first_race_lap=False)[0]) == 5


def test_is_neutralised_codes() -> None:
    assert is_neutralised("124")
    assert is_neutralised("67")
    assert is_neutralised("5")
    assert not is_neutralised("12")
    assert not is_neutralised(None)
    assert not is_neutralised(pd.NA)
    assert not is_neutralised(float("nan"))


def test_fuel_correct_formula() -> None:
    df = pd.DataFrame({"lap_number": [1, 11], "lap_time_s": [90.0, 89.7]})
    out = fuel_correct(df, 0.03)
    assert out["corrected_s"].tolist() == pytest.approx([90.0, 90.0])


def test_insufficient_data() -> None:
    with pytest.raises(InsufficientDataError):
        analyze_stint(synthetic_stint(n=2, start_lap=5))
    with pytest.raises(InsufficientDataError):
        analyze_stint(synthetic_stint(n=0))
    with pytest.raises(InsufficientDataError):
        fit_degradation(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError):
        fit_degradation(np.arange(5.0), np.arange(5.0), confidence=1.5)


def test_band_contains_fit_and_widens_away_from_mean() -> None:
    x = np.arange(1.0, 21.0)
    y = 90 + 0.05 * x + np.random.default_rng(0).normal(0, 0.1, x.size)
    fit = fit_degradation(x, y)
    mid, lo, hi = fit.band(np.array([fit.x_mean, 1.0, 20.0]))
    assert np.all(lo < mid) and np.all(mid < hi)
    assert (hi - lo)[0] < (hi - lo)[1]
    assert np.allclose(fit.predict(np.array([0.0])), fit.intercept)


def test_tyre_age_falls_back_to_lap_index() -> None:
    df = synthetic_stint(n=4, start_lap=7).drop(columns="tyre_life")
    assert tyre_age(df).tolist() == [1, 2, 3, 4]
    df2 = synthetic_stint(n=4, start_lap=7)
    df2.loc[1, "tyre_life"] = np.nan
    assert tyre_age(df2).tolist() == [1, 2, 3, 4]
