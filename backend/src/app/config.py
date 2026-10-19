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

    # --- background execution (Celery + Redis; PostgreSQL stays the source of truth) -------
    redis_url: SecretStr = Field(
        default=SecretStr("redis://127.0.0.1:6380/0"),
        description="Celery broker. Redis holds only transient queue state.",
    )
    worker_concurrency: int = Field(default=2, ge=1, le=64, description="Tasks per worker.")
    max_running_experiments: int = Field(
        default=4, ge=1, description="Experiments allowed to run at once across all workers."
    )
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_backoff_seconds: float = Field(default=5.0, ge=0)
    job_busy_retry_seconds: float = Field(
        default=10.0, ge=0, description="Delay before retrying when the concurrency limit is hit."
    )
    job_lease_seconds: float = Field(
        default=120.0, gt=0, description="Execution lease; renewed after every run."
    )
    job_soft_time_limit_seconds: float = Field(
        default=3600.0, gt=0, description="Wall-clock limit for one experiment job attempt."
    )
    broker_visibility_timeout_seconds: float = Field(
        default=7200.0,
        gt=0,
        description="Redis redelivers unacknowledged tasks after this; keep it above the job "
        "time limit.",
    )
    enqueue_timeout_seconds: float = Field(default=2.0, gt=0)
    agent_plugins: str = Field(
        default="",
        description="Comma-separated modules that register extra agent implementations.",
    )

    # --- per-run limits accepted by the API (defaults and upper bounds) -------------------
    default_run_max_steps: int = Field(default=40, ge=1)
    default_run_max_tool_calls: int = Field(default=12, ge=1)
    default_run_deadline_seconds: float = Field(default=120.0, gt=0)
    max_run_max_steps: int = Field(default=200, ge=1)
    max_run_max_tool_calls: int = Field(default=100, ge=1)
    max_run_deadline_seconds: float = Field(default=900.0, gt=0)
    max_repetitions: int = Field(default=20, ge=1)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
