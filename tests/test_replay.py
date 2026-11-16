"""Replay unit tests and WebSocket protocol tests."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from f1tel import replay as replay_mod
from f1tel.api import create_app
from f1tel.deps import get_db
from f1tel.replay import ReplayData, ReplayState, build_replay, clamp_speed


def make_data() -> ReplayData:
    t = np.arange(0, 11.0)
    df = pd.DataFrame(
        {
            "driver": "AAA",
            "lap_number": 1,
            "t": t,
            "x": t * 10,
            "y": t * 0.0,
            "speed": 100 + t,
        }
    )
    return ReplayData(df)


def test_frame_interpolates_and_respects_gaps() -> None:
    data = make_data()
    (car,) = data.frame_at(2.5)
    assert car["x"] == pytest.approx(25) and car["speed"] == pytest.approx(102.5)
    assert data.frame_at(-1) == []
    assert data.frame_at(100) == []  # beyond the max gap after the last sample


def test_frame_carries_interpolated_lap_distance() -> None:
    df = make_data().samples.assign(distance_m=lambda d: d["t"] * 50.0)
    (car,) = ReplayData(df).frame_at(2.5)
    assert car["distance_m"] == pytest.approx(125.0)
    (plain,) = make_data().frame_at(2.5)  # no distance column: reported as unknown
    assert plain["distance_m"] is None


def test_no_interpolation_across_laps() -> None:
    df = pd.DataFrame(
        {
            "driver": "AAA",
            "lap_number": [1, 2],
            "t": [0.0, 1.0],
            "x": [0.0, 100.0],
            "y": [0.0, 0.0],
            "speed": [100.0, 100.0],
        }
    )
    (car,) = ReplayData(df).frame_at(0.5)
    assert car["x"] == 0 and car["lap"] == 1


def test_state_controls() -> None:
    s = ReplayState(t=5, speed=1)
    s.apply({"action": "pause"}, 0, 10)
    assert not s.playing
    s.apply({"action": "play"}, 0, 10)
    s.apply({"action": "speed", "value": 99}, 0, 10)
    assert s.speed == 20
    s.apply({"action": "speed", "value": 0}, 0, 10)
    assert s.speed == 1
    s.apply({"action": "seek", "t": 50}, 0, 10)
    assert s.t == 10
    for junk in (
        "x",
        None,
        {"action": "seek"},
        {"action": "speed", "value": "fast"},
        {"action": "?"},
    ):
        s.apply(junk, 0, 10)
    assert s.t == 10 and s.speed == 1
    assert clamp_speed(5) == 5


def test_track_outline() -> None:
    out = make_data().track_outline(max_points=4)
    assert len(out["x"]) <= 6 and len(out["x"]) == len(out["y"])
    empty = ReplayData(make_data().samples.assign(x=np.nan))
    assert empty.track_outline() == {"x": [], "y": []}


def test_build_replay_from_fixture(engine: Engine) -> None:
    with Session(engine) as s:
        data = build_replay(s, 1)
        assert data is not None and data.t1 > data.t0
        assert sorted(data.by_driver) == ["AAA", "BBB"]
        assert build_replay(s, 999) is None


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(replay_mod, "TICK_S", 0.001)
    app = create_app()

    def override() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c


def test_ws_streams_frames_in_order(client: TestClient, engine: Engine) -> None:
    with Session(engine) as s:
        data = build_replay(s, 1)
    assert data is not None
    start = data.t1 - 5
    with client.websocket_connect(f"/ws/replay?session=1&speed=20&start={start}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["drivers"] == ["AAA", "BBB"]
        assert len(hello["track"]["x"]) > 10
        assert set(hello["teams"]) == {"AAA", "BBB"}
        assert all(isinstance(v, str) for v in hello["teams"].values())
        times = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "end":
                break
            assert msg["type"] == "frame"
            times.append(msg["t"])
        assert times == sorted(times) and times[-1] <= data.t1


def test_ws_controls_and_disconnect(client: TestClient) -> None:
    with client.websocket_connect("/ws/replay?session=1&speed=1") as ws:
        ws.receive_json()
        ws.send_json({"action": "pause"})
        ws.send_json({"action": "speed", "value": 5})
        seen = False
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "frame" and not msg["playing"] and msg["speed"] == 5:
                seen = True
                break
        assert seen


def test_ws_unknown_session(client: TestClient) -> None:
    with client.websocket_connect("/ws/replay?session=999") as ws:
        assert ws.receive_json()["type"] == "error"


def test_ws_rejects_bad_speed(client: TestClient) -> None:
    with (
        pytest.raises(Exception),  # noqa: B017 - starlette raises WebSocketDisconnect
        client.websocket_connect("/ws/replay?session=1&speed=500"),
    ):
        pass
