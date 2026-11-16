"""Tests for settings loading."""

from pathlib import Path

from f1tel.config import Settings, get_settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.grid_step_m == 10.0
    assert s.raw_dir == Path("data") / "raw"
    assert s.cors_origin_list == ["http://localhost:5173"]


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("F1TEL_GRID_STEP_M", "5")
    monkeypatch.setenv("F1TEL_CORS_ORIGINS", "http://a.test, http://b.test")
    s = Settings(_env_file=None)
    assert s.grid_step_m == 5.0
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]


def test_get_settings_cached() -> None:
    assert get_settings() is get_settings()


def test_database_url_gets_psycopg_driver() -> None:
    for raw in ("postgres://u:p@h/db", "postgresql://u:p@h/db"):
        assert (
            Settings(_env_file=None, database_url=raw).database_url
            == "postgresql+psycopg://u:p@h/db"
        )
    keep = "postgresql+psycopg://u:p@h/db"
    assert Settings(_env_file=None, database_url=keep).database_url == keep
