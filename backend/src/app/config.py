"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Runtime settings. Every field can be overridden with an ``APP_``-prefixed variable."""

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    service_name: str = "ai-reliability-lab-backend"
    environment: Environment = "development"
    database_url: SecretStr | None = Field(
        default=None,
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5433/db. "
        "SecretStr so the password never appears in logs or reprs.",
    )
    trace_retention_days: int = Field(
        default=30, ge=1, description="Trace events older than this may be purged."
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
