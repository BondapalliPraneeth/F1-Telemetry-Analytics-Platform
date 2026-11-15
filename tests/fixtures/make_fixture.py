"""Generate the tiny SYNTHETIC fixture dataset used by the tests.

Nothing here is real F1 data. Two made-up drivers (``AAA`` and ``BBB``) run 14 laps on a
made-up 4 km circuit. The output has exactly the layout the ingestion CLI writes, so it
exercises the real ingest -> database -> API path.

Run (from the repo root)::

    python tests/fixtures/make_fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from f1tel.ingest import ingest_session
from f1tel.loader import LAP_COLUMNS, TELEMETRY_COLUMNS, EventRef

FIXTURE_ROOT = Path(__file__).parent / "raw"
EVENT = EventRef(year=2024, round=1, name="Fixture Grand Prix", location="Testville")
LENGTH_M = 4000.0
CORNERS = [
    {"number": 1, "letter": "", "distance_m": 300.0},
    {"number": 2, "letter": "", "distance_m": 1200.0},
    {"number": 3, "letter": "", "distance_m": 2600.0},
]
N_LAPS = 14
PIT_IN_LAP, PIT_OUT_LAP = 9, 10
SC_LAP = 5
DRIVERS = {"AAA": ("1", "Team Alpha", 0.0), "BBB": ("2", "Team Beta", 1.5)}
TRUE_SLOPE_S_PER_LAP = 0.05  # tyre wear baked into the lap times
FUEL_S_PER_LAP = 0.03  # fuel burn baked into the lap times


def _speed_ms(d: np.ndarray, driver: str, lap: int) -> np.ndarray:
    base = 45.0 + 15.0 * np.sin(2 * np.pi * 3 * d / LENGTH_M)
    scale = 1.0 if driver == "AAA" else 0.99
    scale *= 1.0 + 0.0005 * (lap % 3)
    v = base * scale
    if driver == "BBB":  # BBB loses time on the last third of the lap
        v = np.where(d > 2600.0, v * 0.92, v)
    return v


def lap_telemetry(driver: str, lap: int) -> pd.DataFrame:
    """Synthetic raw telemetry for one lap (irregular ~7 m sampling, like real data)."""
    rng = np.random.default_rng(lap * 7 + ord(driver[0]))
    d = np.unique(np.concatenate(([0.0], np.cumsum(rng.uniform(5.0, 9.0, 700)))))
    d = d[d <= LENGTH_M]
    v = _speed_ms(d, driver, lap)
    dt = np.diff(d) / ((v[1:] + v[:-1]) / 2)
    t = np.concatenate(([0.0], np.cumsum(dt)))
    ang = 2 * np.pi * d / LENGTH_M
    radius = LENGTH_M / (2 * np.pi)
    accel = np.gradient(v, d)
    return pd.DataFrame(
        {
            "Time_s": t,
            "Distance": d,
            "Speed": (v * 3.6).astype("float32"),
            "Throttle": np.clip(60 + 40 * np.sign(accel), 0, 100).astype("float32"),
            "Brake": (accel < -0.05).astype("float32"),
            "nGear": np.clip(np.floor(v / 9), 1, 8).astype("float32"),
            "RPM": (9000 + 40 * v).astype("float32"),
            "DRS": np.zeros_like(d, dtype="float32"),
            "X": (radius * np.cos(ang)).astype("float32"),
            "Y": (radius * np.sin(ang)).astype("float32"),
        }
    )[TELEMETRY_COLUMNS]


def build_laps() -> pd.DataFrame:
    """The synthetic lap table (all 14 laps for both drivers)."""
    rows = []
    for code, (number, team, offset) in DRIVERS.items():
        for lap in range(1, N_LAPS + 1):
            stint = 1 if lap <= PIT_IN_LAP else 2
            age = lap if stint == 1 else lap - PIT_OUT_LAP + 1
            wiggle = 0.02 * np.sin(lap * 1.7 + (0 if code == "AAA" else 1))
            lap_time = 89.0 + TRUE_SLOPE_S_PER_LAP * age - FUEL_S_PER_LAP * (lap - 1) + wiggle
            if code == "BBB":
                lap_time += 0.4
            pit_in, pit_out = lap == PIT_IN_LAP, lap == PIT_OUT_LAP
            sc = lap == SC_LAP
            rows.append(
                {
                    "driver": code,
                    "driver_number": number,
                    "team": team,
                    "lap_number": lap,
                    "lap_time_s": lap_time + (25.0 if sc else 0.0),
                    "s1_s": lap_time * 0.33,
                    "s2_s": lap_time * 0.34,
                    "s3_s": lap_time * 0.33,
                    "stint": stint,
                    "compound": "MEDIUM" if stint == 1 else "HARD",
                    "tyre_life": float(age),
                    "fresh_tyre": True,
                    "track_status": "14" if sc else "1",
                    "is_accurate": not (pit_in or pit_out or sc or lap == 1),
                    "pit_in": pit_in,
                    "pit_out": pit_out,
                    "position": 1.0 if code == "AAA" else 2.0,
                    "lap_start_s": (lap - 1) * 92.0 + offset,
                }
            )
    return pd.DataFrame(rows)[LAP_COLUMNS].astype({"lap_number": "Int64", "stint": "Int64"})


class FakeSource:
    """A :class:`f1tel.loader.SessionSource` serving the synthetic data."""

    def laps(self) -> pd.DataFrame:
        """All synthetic laps."""
        return build_laps()

    def telemetry(self, driver: str, lap_number: int) -> pd.DataFrame:
        """Synthetic telemetry for one lap."""
        return lap_telemetry(driver, lap_number)

    def weather(self) -> pd.DataFrame | None:
        """No weather in the fixture."""
        return None

    def corners(self) -> list[dict]:
        """Made-up corners."""
        return CORNERS


def main(out: Path = FIXTURE_ROOT) -> None:
    """Write the fixture dataset to ``out`` using the real ingestion code."""
    rep = ingest_session(FakeSource(), EVENT, "R", out, force=True)
    print(f"wrote {rep.written} laps ({rep.rows} rows) to {rep.session_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE_ROOT)
