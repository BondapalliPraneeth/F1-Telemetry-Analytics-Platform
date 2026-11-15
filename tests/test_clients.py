"""Tests for the Jolpica/OpenF1 clients, their cache, and the cross-check logic."""

from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd
import pytest

from f1tel.clients._http import CachedJSONClient
from f1tel.clients.jolpica import JolpicaClient
from f1tel.clients.openf1 import OpenF1Client
from f1tel.crosscheck import compare_lap_times, compare_pit_stops
from f1tel.retry import RetryableHTTPError


def test_cache_hit_avoids_network(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"a": 1})

    c = CachedJSONClient(
        "https://x.test", cache_dir=tmp_path, transport=httpx.MockTransport(handler)
    )
    assert c.get_json("/thing", {"q": 1}) == {"a": 1}
    assert c.get_json("thing", {"q": 1}) == {"a": 1}
    assert len(calls) == 1
    c.get_json("thing", {"q": 2})
    assert len(calls) == 2
    c.close()


def test_retries_on_503_then_succeeds() -> None:
    seq = iter([503, 429, 200])
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        code = next(seq)
        return httpx.Response(code, json={"ok": True})

    c = CachedJSONClient(
        "https://x.test", transport=httpx.MockTransport(handler), retries=3, sleep=sleeps.append
    )
    assert c.get_json("x") == {"ok": True}
    assert sleeps == [1.0, 2.0]


def test_gives_up_and_4xx_not_retried() -> None:
    c = CachedJSONClient(
        "https://x.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
        retries=1,
        sleep=lambda s: None,
    )
    with pytest.raises(RetryableHTTPError):
        c.get_json("x")
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    c404 = CachedJSONClient("https://x.test", transport=httpx.MockTransport(handler), retries=3)
    with pytest.raises(httpx.HTTPStatusError):
        c404.get_json("x")
    assert calls["n"] == 1


def test_timeout_is_retried() -> None:
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json=[1])

    c = CachedJSONClient(
        "https://x.test", transport=httpx.MockTransport(handler), retries=2, sleep=lambda s: None
    )
    assert c.get_json("x") == [1]


def test_jolpica_pitstops_paginate_and_results_empty() -> None:
    pages = {
        "0": {
            "total": "150",
            "Races": [
                {"PitStops": [{"driverId": "a", "stop": "1", "lap": "10", "duration": "21.5"}]}
            ],
        },
        "100": {
            "total": "150",
            "Races": [
                {"PitStops": [{"driverId": "b", "stop": "1", "lap": "12", "duration": "1:02.3"}]}
            ],
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if "pitstops" in req.url.path:
            p = pages[req.url.params["offset"]]
            return httpx.Response(
                200, json={"MRData": {"total": p["total"], "RaceTable": {"Races": p["Races"]}}}
            )
        return httpx.Response(200, json={"MRData": {"RaceTable": {"Races": []}}})

    j = JolpicaClient("https://x.test", transport=httpx.MockTransport(handler))
    stops = j.pit_stops(2024, 1)
    assert [s["driver_id"] for s in stops] == ["a", "b"]
    assert stops[0]["duration_s"] == 21.5 and stops[1]["duration_s"] is None
    assert j.results(2024, 1) == []


def test_openf1_session_and_laps() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/sessions"):
            assert req.url.params["session_name"] == "Qualifying"
            return httpx.Response(200, json=[{"session_key": 9558}])
        return httpx.Response(
            200,
            json=[
                {"driver_number": 1, "lap_number": 3, "lap_duration": 80.0},
                {"driver_number": 1, "lap_number": 4, "lap_duration": None},
            ],
        )

    o = OpenF1Client("https://x.test/v1", transport=httpx.MockTransport(handler))
    assert o.find_session_key(2024, "Monza", "Q") == 9558
    laps = o.laps(9558)
    assert laps[0] == {"driver_number": "1", "lap_number": 3, "lap_duration": 80.0}
    empty = OpenF1Client(
        "https://x.test/v1", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    )
    assert empty.find_session_key(2024, "Nowhere", "R") is None


def test_compare_lap_times() -> None:
    ff1 = pd.DataFrame(
        {
            "driver_number": ["1", "1", "1", "2"],
            "lap_number": [1, 2, 3, 1],
            "lap_time_s": [90.0, 91.0, None, 95.0],
        }
    )
    other = [
        {"driver_number": "1", "lap_number": 1, "lap_duration": 90.01},
        {"driver_number": "1", "lap_number": 2, "lap_duration": 91.5},
        {"driver_number": "1", "lap_number": 3, "lap_duration": 92.0},
    ]
    chk = compare_lap_times(ff1, other, tol_s=0.05)
    assert chk.compared == 2 and chk.within_tolerance == 1
    assert chk.max_abs_diff_s == pytest.approx(0.5)
    assert chk.agreement == 0.5
    none = compare_lap_times(ff1, [])
    assert none.compared == 0 and none.agreement is None
    assert none.to_dict()["agreement"] is None


def test_compare_pit_stops() -> None:
    laps = pd.DataFrame({"driver": ["AAA", "AAA", "BBB"], "pit_in": [True, True, False]})
    stops = [
        {"driver_id": "a", "stop": 1},
        {"driver_id": "a", "stop": 2},
        {"driver_id": "b", "stop": 1},
    ]
    out = compare_pit_stops(laps, stops, {"a": "AAA", "b": "BBB"})
    assert out == {"AAA": {"fastf1": 2, "jolpica": 2}, "BBB": {"fastf1": 0, "jolpica": 1}}
