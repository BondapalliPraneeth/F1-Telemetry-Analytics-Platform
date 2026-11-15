"""Tests for ingestion: idempotency, checksums, retries, batch planning and the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pandas as pd
import pytest
from make_fixture import EVENT, FakeSource
from typer.testing import CliRunner

from f1tel import ingest as ingest_mod
from f1tel import loader
from f1tel.config import Settings
from f1tel.ingest import (
    app,
    batch_plan,
    ingest_one,
    ingest_session,
    parse_int_list,
    session_dir,
)
from f1tel.loader import (
    SessionSource,
    normalize_laps,
    normalize_telemetry,
    session_code,
    slugify,
)
from f1tel.manifest import Manifest, sha256_file
from f1tel.retry import RetryableHTTPError, retry_call

runner = CliRunner()


def test_ingest_writes_accurate_laps_only(tmp_path: Path) -> None:
    rep = ingest_session(FakeSource(), EVENT, "R", tmp_path)
    laps = FakeSource().laps()
    n_accurate = int(laps["is_accurate"].sum())
    assert rep.written == n_accurate
    assert rep.skipped == 0 and not rep.failed
    sdir = session_dir(tmp_path, EVENT, "R")
    assert sdir == tmp_path / "2024" / "Fixture_Grand_Prix" / "R"
    files = sorted(p.name for p in (sdir / "AAA").glob("lap_*.parquet"))
    assert "lap_9.parquet" not in files  # pit-in lap is not accurate
    assert "lap_3.parquet" in files
    m = Manifest.load(sdir)
    assert m.complete
    assert m.verify() == []


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    ingest_session(FakeSource(), EVENT, "R", tmp_path)
    sdir = session_dir(tmp_path, EVENT, "R")
    before = {p: sha256_file(p) for p in sdir.rglob("lap_*.parquet")}
    mtimes = {p: p.stat().st_mtime_ns for p in before}
    rep = ingest_session(FakeSource(), EVENT, "R", tmp_path)
    assert rep.written == 0
    assert rep.skipped == len(before)
    assert {p: p.stat().st_mtime_ns for p in before} == mtimes


def test_corrupt_file_is_rewritten(tmp_path: Path) -> None:
    ingest_session(FakeSource(), EVENT, "R", tmp_path)
    sdir = session_dir(tmp_path, EVENT, "R")
    victim = sdir / "AAA" / "lap_3.parquet"
    good = sha256_file(victim)
    victim.write_bytes(b"garbage")
    assert Manifest.load(sdir).verify() == ["AAA/lap_3.parquet"]
    rep = ingest_session(FakeSource(), EVENT, "R", tmp_path)
    assert rep.written == 1
    assert sha256_file(victim) == good


def test_force_rewrites_everything(tmp_path: Path) -> None:
    first = ingest_session(FakeSource(), EVENT, "R", tmp_path)
    again = ingest_session(FakeSource(), EVENT, "R", tmp_path, force=True)
    assert again.written == first.written


class FlakySource(FakeSource):
    """Fails telemetry for one lap, returns empty for another."""

    def telemetry(self, driver: str, lap_number: int) -> pd.DataFrame:
        if (driver, lap_number) == ("AAA", 3):
            raise KeyError("no data")
        if (driver, lap_number) == ("AAA", 4):
            return pd.DataFrame()
        return super().telemetry(driver, lap_number)


def test_failed_laps_are_reported_and_session_not_complete(tmp_path: Path) -> None:
    rep = ingest_session(FlakySource(), EVENT, "R", tmp_path)
    assert len(rep.failed) == 2
    assert not Manifest.load(rep.session_dir).complete
    # a later clean run resumes and completes
    rep2 = ingest_session(FakeSource(), EVENT, "R", tmp_path)
    assert rep2.written == 2
    assert Manifest.load(rep.session_dir).complete


def test_progress_callback(tmp_path: Path) -> None:
    msgs: list[str] = []
    ingest_session(FakeSource(), EVENT, "R", tmp_path, progress=msgs.append)
    assert msgs == []  # fewer than 50 laps -> no progress lines


def settings_for(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", http_max_retries=0)


def test_ingest_one_skips_complete_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest_mod, "fetch_external", lambda *a, **k: None)
    calls = {"n": 0}

    def factory(ref, code) -> SessionSource:
        calls["n"] += 1
        return FakeSource()

    s = settings_for(tmp_path)
    resolver = lambda y, e: EVENT  # noqa: E731
    first = ingest_one(2024, "Fixture", "R", s, resolver=resolver, source_factory=factory)
    assert first.written > 0 and not first.already_complete
    second = ingest_one(2024, "Fixture", "r", s, resolver=resolver, source_factory=factory)
    assert second.already_complete
    assert calls["n"] == 1  # the second run never loaded the session
    third = ingest_one(
        2024, "Fixture", "R", s, force=True, resolver=resolver, source_factory=factory
    )
    assert calls["n"] == 2 and third.written == first.written


def test_fetch_external_writes_and_tolerates_errors(tmp_path: Path) -> None:
    from f1tel.clients.jolpica import JolpicaClient

    def handler(request: httpx.Request) -> httpx.Response:
        if "results" in request.url.path:
            return httpx.Response(200, json=RESULTS_JSON)
        return httpx.Response(200, json=PITS_JSON)

    client = JolpicaClient("https://x.test", transport=httpx.MockTransport(handler), retries=0)
    sdir = tmp_path / "s"
    ingest_mod.fetch_external(EVENT, "R", sdir, settings_for(tmp_path), client=client)
    assert json.loads((sdir / "results.json").read_text())[0]["code"] == "AAA"
    assert Manifest.load(sdir).verify() == []
    ingest_mod.fetch_external(EVENT, "Q", tmp_path / "q", settings_for(tmp_path), client=client)
    assert not (tmp_path / "q").exists()  # qualifying has no race data

    bad = JolpicaClient(
        "https://x.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(404)),
        retries=0,
    )
    ingest_mod.fetch_external(EVENT, "R", tmp_path / "bad", settings_for(tmp_path), client=bad)
    assert not (tmp_path / "bad" / "results.json").exists()


RESULTS_JSON = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "Results": [
                        {
                            "number": "1",
                            "position": "1",
                            "grid": "1",
                            "laps": "14",
                            "status": "Finished",
                            "points": "25",
                            "Driver": {"driverId": "aaa_driver", "code": "AAA"},
                            "Constructor": {"name": "Team Alpha"},
                        }
                    ]
                }
            ]
        }
    }
}
PITS_JSON = {
    "MRData": {
        "total": "1",
        "RaceTable": {
            "Races": [
                {
                    "PitStops": [
                        {"driverId": "aaa_driver", "stop": "1", "lap": "9", "duration": "22.1"}
                    ]
                }
            ]
        },
    }
}


def test_cli_ingest_and_missing_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("F1_USERNAME", raising=False)
    monkeypatch.delenv("F1_PASSWORD", raising=False)
    monkeypatch.setattr(
        "fastf1.internals.f1account.token_manager.get_stored", lambda: None, raising=False
    )
    with pytest.raises(loader.MissingCredentialsError):
        loader.require_credentials()
    monkeypatch.setenv("F1_USERNAME", "u")
    monkeypatch.setenv("F1_PASSWORD", "p")
    loader.require_credentials()  # no raise

    monkeypatch.setattr(loader, "configure_cache", lambda d: d)
    monkeypatch.setattr(loader, "resolve_event", lambda y, e: EVENT)
    monkeypatch.setattr(loader, "FastF1Source", lambda ref, code: FakeSource())
    monkeypatch.setattr(ingest_mod, "fetch_external", lambda *a, **k: None)
    monkeypatch.setenv("F1TEL_FASTF1_CACHE_DIR", str(tmp_path / "cache"))
    res = runner.invoke(
        app,
        [
            "ingest",
            "--year",
            "2024",
            "--event",
            "Fixture",
            "--session",
            "R",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "wrote" in res.output
    res = runner.invoke(
        app,
        [
            "ingest",
            "--year",
            "2024",
            "--event",
            "Fixture",
            "--session",
            "R",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert "already complete" in res.output
    res = runner.invoke(app, ["verify", "--data-dir", str(tmp_path)])
    assert res.exit_code == 0 and "ok" in res.output


def test_cli_batch_continues_after_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("F1_USERNAME", "u")
    monkeypatch.setenv("F1_PASSWORD", "p")
    monkeypatch.setattr(loader, "configure_cache", lambda d: d)
    monkeypatch.setattr(ingest_mod, "fetch_external", lambda *a, **k: None)

    def resolver(year: int, event: str):
        if event == "2":
            raise RuntimeError("boom")
        return EVENT

    monkeypatch.setattr(loader, "resolve_event", resolver)
    monkeypatch.setattr(loader, "FastF1Source", lambda ref, code: FakeSource())
    res = runner.invoke(
        app,
        [
            "batch",
            "--years",
            "2024",
            "--rounds",
            "1-2",
            "--sessions",
            "R",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 1
    assert "2 sessions planned" in res.output
    assert "FAILED" in res.output and "1 session(s) with failures" in res.output


def test_retry_call_backoff_and_giveup() -> None:
    delays: list[float] = []
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("down")
        return "ok"

    assert retry_call(flaky, retries=4, base_delay=1.0, sleep=delays.append) == "ok"
    assert delays == [1.0, 2.0]

    def always() -> None:
        raise RetryableHTTPError("503")

    with pytest.raises(RetryableHTTPError):
        retry_call(always, retries=2, base_delay=0.5, max_delay=0.7, sleep=delays.append)
    assert delays[2:] == [0.5, 0.7]

    with pytest.raises(ZeroDivisionError):  # non-network errors are not retried
        retry_call(lambda: 1 / 0, retries=3, sleep=delays.append)


def test_helpers() -> None:
    assert parse_int_list("1-3,7") == [1, 2, 3, 7]
    assert batch_plan([2023, 2024], [1, 2], ["r", "Q"]) == [
        (2023, "1", "R"),
        (2023, "1", "Q"),
        (2023, "2", "R"),
        (2023, "2", "Q"),
        (2024, "1", "R"),
        (2024, "1", "Q"),
        (2024, "2", "R"),
        (2024, "2", "Q"),
    ]
    assert len(batch_plan([2023, 2024, 2025], range(1, 9), ["R"])) == 24
    assert slugify("Italian Grand Prix") == "Italian_Grand_Prix"
    with pytest.raises(ValueError):
        slugify("  ")
    assert session_code("qualifying") == "Q"
    with pytest.raises(ValueError):
        session_code("FP1")
    assert len(ingest_mod.SEED_SESSIONS) == 5
    assert ("Monza", "Q") in {(e, s) for _, e, s in ingest_mod.SEED_SESSIONS}


def test_normalize_laps_and_telemetry() -> None:
    raw = pd.DataFrame(
        {
            "Driver": ["AAA", "AAA"],
            "DriverNumber": ["1", "1"],
            "LapNumber": [1.0, 2.0],
            "LapTime": pd.to_timedelta([90.5, None], unit="s"),
            "IsAccurate": [True, None],
            "PitInTime": pd.to_timedelta([None, 5], unit="s"),
            "TrackStatus": ["1", "4"],
        }
    )
    out = normalize_laps(raw)
    assert list(out.columns) == loader.LAP_COLUMNS
    assert out["lap_time_s"].iloc[0] == 90.5 and pd.isna(out["lap_time_s"].iloc[1])
    assert out["is_accurate"].tolist() == [True, False]
    assert out["pit_in"].tolist() == [False, True]

    tel = normalize_telemetry(
        pd.DataFrame(
            {
                "Time": pd.to_timedelta([0, 1], unit="s"),
                "Distance": [0.0, 10.0],
                "Speed": [100, 110],
                "Brake": [False, True],
            }
        )
    )
    assert list(tel.columns) == loader.TELEMETRY_COLUMNS
    assert tel["Brake"].tolist() == [0.0, 1.0]
    assert tel["Time_s"].tolist() == [0.0, 1.0]
    assert tel["nGear"].isna().all()
