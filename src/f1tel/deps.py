"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from f1tel.config import get_settings
from f1tel.db import get_engine, make_session_factory


@lru_cache
def _factory(url: str) -> sessionmaker:
    return make_session_factory(get_engine(url))


def get_db() -> Iterator[Session]:
    """Yield a database session bound to the configured database (tests override this)."""
    with _factory(get_settings().database_url)() as session:
        yield session
