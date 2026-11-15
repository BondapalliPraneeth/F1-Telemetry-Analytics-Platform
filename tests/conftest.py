"""Shared fixtures: the synthetic dataset loaded into an in-memory SQLite database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from f1tel.db import Base
from f1tel.dbload import load_session_dir

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

FIXTURE_SESSION = FIXTURES / "raw" / "2024" / "Fixture_Grand_Prix" / "R"


@pytest.fixture(scope="session")
def fixture_session_dir() -> Path:
    """Directory of the checked-in synthetic session."""
    return FIXTURE_SESSION


@pytest.fixture
def engine(fixture_session_dir: Path) -> Engine:
    """In-memory SQLite engine with the fixture session loaded."""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    load_session_dir(eng, fixture_session_dir)
    return eng
