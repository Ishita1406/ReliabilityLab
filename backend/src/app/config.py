"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]
JudgeProvider = Literal["none", "groq"]


class Settings(BaseSettings):
    """Runtime settings. Every field can be overridden with an ``APP_``-prefixed variable."""

    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=".env", extra="ignore", populate_by_name=True
    )

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

    # --- optional LLM judge (rubric criteria, diagnosis suggestions) -----------------------
    judge_provider: JudgeProvider = Field(
        default="none",
        description="'none' disables the LLM judge; rubric criteria are then "
        "reported as inconclusive.",
    )
    groq_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_GROQ_API_KEY", "GROQ_API_KEY"),
        description="Groq API key; read from APP_GROQ_API_KEY or GROQ_API_KEY.",
    )
    groq_model: str = Field(default="llama-3.3-70b-versatile", min_length=1)
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", min_length=1)
    judge_timeout_seconds: float = Field(default=30.0, gt=0)
    judge_samples: int = Field(
        default=1, ge=1, le=5, description="Independent judge calls per rubric criterion."
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
