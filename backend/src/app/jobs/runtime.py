"""Process-wide dependencies of the worker (sessions, settings), replaceable in tests."""

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.session import create_db_engine, database_url, session_factory
from app.jobs.executor import JobExecutor, JobSettings
from app.jobs.registry import default_evaluator, load_plugins


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    sessions: sessionmaker[Session]

    def executor(self) -> JobExecutor:
        return JobExecutor(
            self.sessions,
            settings=JobSettings(
                lease_seconds=self.settings.job_lease_seconds,
                max_running=self.settings.max_running_experiments,
            ),
            evaluator_factory=lambda: default_evaluator(self.settings),
        )


_override: Runtime | None = None


@lru_cache
def _default() -> Runtime:
    settings = get_settings()
    load_plugins(settings.agent_plugins)
    return Runtime(settings, session_factory(create_db_engine(database_url(settings))))


def get_runtime() -> Runtime:
    return _override or _default()


def use_runtime(runtime: Runtime | None) -> None:
    """Install (or with ``None`` remove) a runtime, e.g. one bound to a test database."""
    global _override
    _override = runtime
