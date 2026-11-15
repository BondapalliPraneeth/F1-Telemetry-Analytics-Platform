"""HTTP API tests against the synthetic session in an in-memory database."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from f1tel.api import create_app
from f1tel.deps import get_db


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app()

    def override() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c


def test_health_and_sessions(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    rows = client.get("/sessions").json()
    assert len(rows) == 1 and rows[0]["event"] == "Fixture Grand Prix"
    sid = rows[0]["id"]
    drivers = client.get("/drivers", params={"session": sid}).json()
    assert [d["code"] for d in drivers] == ["AAA", "BBB"]


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.get("/drivers", params={"session": 999}).status_code == 404
    assert client.get("/laps", params={"session": 999, "driver": "AAA"}).status_code == 404


def test_laps_and_stints(client: TestClient) -> None:
    laps = client.get("/laps", params={"session": 1, "driver": "aaa"}).json()
    assert len(laps) == 14
    tel = client.get(
        "/laps", params={"session": 1, "driver": "AAA", "only_with_telemetry": True}
    ).json()
    assert 0 < len(tel) < len(laps)
    stints = client.get("/stints", params={"session": 1, "driver": "AAA"}).json()
    assert stints[0]["lap_start"] == 1 and stints[0]["n_laps"] == 9


def test_compare_defaults_to_fastest_laps(client: TestClient) -> None:
    r = client.get("/compare", params={"session": 1, "a": "AAA", "b": "BBB"})
    assert r.status_code == 200
    body = r.json()
    n = len(body["distance"])
    assert n > 300 and len(body["delta"]) == n
    assert body["delta"][0] == 0
    assert len(body["a"]["speed"]) == n
    assert len(body["mini_sectors"]) == 20
    assert [s["name"] for s in body["sectors"]] == ["S1", "S2", "S3", "Lap"]
    assert len(body["corners"]) == 3
    total = sum(m["delta_s"] for m in body["mini_sectors"])
    assert total == pytest.approx(body["final_delta_s"], abs=1e-6)


def test_compare_same_lap_has_zero_delta(client: TestClient) -> None:
    body = client.get("/compare", params={"session": 1, "a": "AAA", "b": "AAA"}).json()
    assert max(abs(d) for d in body["delta"]) == pytest.approx(0, abs=1e-9)


def test_compare_missing_lap_is_404(client: TestClient) -> None:
    r = client.get("/compare", params={"session": 1, "a": "AAA", "b": "BBB", "lap_a": 99})
    assert r.status_code == 404
    r = client.get("/compare", params={"session": 1, "a": "AAA", "b": "ZZZ"})
    assert r.status_code == 404


def test_compare_validation(client: TestClient) -> None:
    r = client.get("/compare", params={"session": 1, "a": "AAA", "b": "BBB", "mini": 0})
    assert r.status_code == 422


def test_degradation_recovers_planted_slope(client: TestClient) -> None:
    r = client.get("/degradation", params={"session": 1, "driver": "AAA", "stint": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["fit"]["ci_low"] <= 0.05 <= body["fit"]["ci_high"]
    assert body["cleaning"]["kept"] == len(body["points"])
    assert len(body["band"]) == 25
    assert all(p["low"] <= p["fit"] <= p["high"] for p in body["band"])


def test_degradation_errors(client: TestClient) -> None:
    assert (
        client.get("/degradation", params={"session": 1, "driver": "AAA", "stint": 99}).status_code
        == 404
    )
    bad = client.get(
        "/degradation",
        params={"session": 1, "driver": "AAA", "stint": 1, "fuel_effect_s_per_lap": 5},
    )
    assert bad.status_code == 422


def test_database_failure_maps_to_503(engine: Engine) -> None:
    app = create_app()

    def broken() -> Iterator[Session]:
        raise OperationalError("SELECT 1", {}, Exception("down"))
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/health").status_code == 503
