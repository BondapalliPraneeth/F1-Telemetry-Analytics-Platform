"""Application settings read from environment variables (prefix ``F1TEL_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from the environment or a local ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="F1TEL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://f1tel:f1tel@localhost:5432/f1tel"
    data_dir: Path = Path("data")
    fastf1_cache_dir: Path = Path(".fastf1-cache")
    grid_step_m: float = Field(default=10.0, gt=0)
    http_timeout_s: float = Field(default=20.0, gt=0)
    http_max_retries: int = Field(default=4, ge=0)
    jolpica_base_url: str = "https://api.jolpi.ca/ergast/f1"
    openf1_base_url: str = "https://api.openf1.org/v1"
    cors_origins: str = "http://localhost:5173"
    fuel_effect_s_per_lap: float = 0.03

    @property
    def raw_dir(self) -> Path:
        """Directory holding per-lap Parquet telemetry."""
        return self.data_dir / "raw"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
