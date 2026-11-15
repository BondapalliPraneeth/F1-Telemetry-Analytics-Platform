"""WebSocket session replay: stream stored samples in session-time order.

``/ws/replay?session=<id>&speed=<1..20>`` sends a ``hello`` message (time range, drivers and
a track outline), then ``frame`` messages every ``TICK_S`` seconds of wall time. Session
time advances ``speed`` times faster than wall time. The client may send JSON controls::

    {"action": "pause"} {"action": "play"} {"action": "speed", "value": 5}
    {"action": "seek", "t": 1234.5}

Replay uses the stored resampled telemetry of accurate laps, so a car is absent from a
frame while it is on a lap that was not ingested (in/out laps, safety-car laps).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Annotated, Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from f1tel.db import LapRow, SessionRow, TelemetryResampled
from f1tel.deps import get_db

router = APIRouter(tags=["replay"])

TICK_S = 0.1
MIN_SPEED, MAX_SPEED = 1.0, 20.0
MAX_GAP_S = 3.0  # a car with no sample within this window is not drawn


def clamp_speed(value: float) -> float:
    """Clamp a replay speed to the supported 1x-20x range."""
    return float(min(MAX_SPEED, max(MIN_SPEED, value)))


@dataclass
class ReplayState:
    """Mutable playback state controlled by client messages."""

    t: float
    speed: float
    playing: bool = True

    def apply(self, msg: Any, t0: float, t1: float) -> None:
        """Apply one client control message; malformed messages are ignored."""
        if not isinstance(msg, dict):
            return
        action = msg.get("action")
        try:
            if action == "pause":
                self.playing = False
            elif action == "play":
                self.playing = True
            elif action == "speed":
                self.speed = clamp_speed(float(msg["value"]))
            elif action == "seek":
                self.t = float(min(t1, max(t0, float(msg["t"]))))
        except (KeyError, TypeError, ValueError):
            return


class ReplayData:
    """All samples of a session, indexed per driver by absolute session time."""

    def __init__(self, samples: pd.DataFrame) -> None:
        """``samples``: columns driver, lap_number, t (session s), x, y, speed."""
        self.by_driver: dict[str, dict[str, np.ndarray]] = {}
        for driver, g in samples.sort_values("t").groupby("driver"):
            self.by_driver[str(driver)] = {
                c: g[c].to_numpy(dtype=float) for c in ("t", "lap_number", "x", "y", "speed")
            }
        self.t0 = float(samples["t"].min())
        self.t1 = float(samples["t"].max())
        self.samples = samples

    def frame_at(self, t: float) -> list[dict[str, Any]]:
        """Car positions at session time ``t`` (linear interpolation within a lap)."""
        cars = []
        for driver, a in self.by_driver.items():
            i = int(np.searchsorted(a["t"], t, side="right")) - 1
            if i < 0 or t - a["t"][i] > MAX_GAP_S:
                continue
            x, y, spd = a["x"][i], a["y"][i], a["speed"][i]
            j = i + 1
            if (
                j < a["t"].size
                and a["lap_number"][j] == a["lap_number"][i]
                and a["t"][j] > a["t"][i]
            ):
                w = (t - a["t"][i]) / (a["t"][j] - a["t"][i])
                x += w * (a["x"][j] - x)
                y += w * (a["y"][j] - y)
                spd += w * (a["speed"][j] - spd)
            if np.isfinite(x) and np.isfinite(y):
                cars.append(
                    {
                        "driver": driver,
                        "x": float(x),
                        "y": float(y),
                        "speed": None if not np.isfinite(spd) else float(spd),
                        "lap": int(a["lap_number"][i]),
                    }
                )
        return cars

    def track_outline(self, max_points: int = 400) -> dict[str, list[float]]:
        """x/y of one complete lap (the one with most samples), downsampled for the client."""
        g = self.samples.dropna(subset=["x", "y"])
        if g.empty:
            return {"x": [], "y": []}
        key = g.groupby(["driver", "lap_number"]).size().idxmax()
        lap = g[(g.driver == key[0]) & (g.lap_number == key[1])].sort_values("t")
        step = max(1, len(lap) // max_points)
        lap = lap.iloc[::step]
        return {"x": lap["x"].astype(float).tolist(), "y": lap["y"].astype(float).tolist()}


def build_replay(db: Session, session_id: int) -> ReplayData | None:
    """Load a session's telemetry with absolute session time, or ``None`` if there is none."""
    q = (
        select(
            TelemetryResampled.driver,
            TelemetryResampled.lap_number,
            (LapRow.lap_start_s + TelemetryResampled.time_s).label("t"),
            TelemetryResampled.x,
            TelemetryResampled.y,
            TelemetryResampled.speed,
        )
        .join(
            LapRow,
            (LapRow.session_id == TelemetryResampled.session_id)
            & (LapRow.driver == TelemetryResampled.driver)
            & (LapRow.lap_number == TelemetryResampled.lap_number),
        )
        .where(TelemetryResampled.session_id == session_id, LapRow.lap_start_s.is_not(None))
    )
    df = pd.DataFrame(db.execute(q).all(), columns=["driver", "lap_number", "t", "x", "y", "speed"])
    return ReplayData(df) if len(df) else None


async def _read_controls(
    ws: WebSocket, state: ReplayState, data: ReplayData, stop: asyncio.Event
) -> None:
    try:
        while True:
            state.apply(await ws.receive_json(), data.t0, data.t1)
    except (WebSocketDisconnect, RuntimeError):
        stop.set()
    except ValueError:  # invalid JSON from the client: keep listening
        await _read_controls(ws, state, data, stop)


@router.websocket("/ws/replay")
async def replay(
    ws: WebSocket,
    session: Annotated[int, Query(description="session id")],
    speed: Annotated[float, Query(ge=MIN_SPEED, le=MAX_SPEED)] = 1.0,
    start: Annotated[float | None, Query(description="session time to start from (s)")] = None,
    db: Session = Depends(get_db),
) -> None:
    """Stream replay frames for a session; see the module docstring for the protocol."""
    await ws.accept()
    if db.get(SessionRow, session) is None:
        await ws.send_json({"type": "error", "detail": f"session {session} not found"})
        await ws.close(code=1008)
        return
    data = await run_in_threadpool(build_replay, db, session)
    if data is None:
        await ws.send_json({"type": "error", "detail": "session has no telemetry with timing"})
        await ws.close(code=1008)
        return

    t_start = data.t0 if start is None else min(max(start, data.t0), data.t1)
    state = ReplayState(t=t_start, speed=speed)
    stop = asyncio.Event()
    reader = asyncio.create_task(_read_controls(ws, state, data, stop))
    try:
        await ws.send_json(
            {
                "type": "hello",
                "t0": data.t0,
                "t1": data.t1,
                "speed": state.speed,
                "drivers": sorted(data.by_driver),
                "track": data.track_outline(),
            }
        )
        loop = asyncio.get_running_loop()
        last = loop.time()
        while state.t < data.t1 and not stop.is_set():
            now = loop.time()
            if state.playing:
                state.t = min(data.t1, state.t + (now - last) * state.speed)
            last = now
            await ws.send_json(
                {
                    "type": "frame",
                    "t": state.t,
                    "speed": state.speed,
                    "playing": state.playing,
                    "cars": data.frame_at(state.t),
                }
            )
            await asyncio.sleep(TICK_S)
        if not stop.is_set():
            await ws.send_json({"type": "end", "t": state.t})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        with contextlib.suppress(RuntimeError):
            await ws.close()
