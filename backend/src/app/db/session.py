"""Engine and session factories."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """``APP_DATABASE_URL`` is not set."""


def database_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.database_url is None:
        raise DatabaseNotConfiguredError(
            "APP_DATABASE_URL is not set; see .env.example and docs/engineering/05"
        )
    return settings.database_url.get_secret_value()


def create_db_engine(url: str) -> Engine:
    # hide_parameters keeps bound values (which may contain payloads) out of error messages.
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
