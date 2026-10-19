"""PostgreSQL test fixtures.

* A throwaway database ``reliabilitylab_test_<hex>`` is created per test session on the server given
  by ``APP_TEST_DATABASE_URL`` (falling back to ``APP_DATABASE_URL``, then the local compose
  default), migrated with Alembic, and dropped afterwards.
* Each test runs inside an outer transaction that is rolled back at the end; code under test
  that "commits" only releases a SAVEPOINT (``join_transaction_mode="create_savepoint"``).
* If the server is unreachable, database tests are skipped with the reason, or fail when
  ``APP_REQUIRE_POSTGRES=1`` (for CI).
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
LOCAL_DEFAULT_URL = (
    "postgresql+psycopg://reliabilitylab:reliabilitylab_local_dev@127.0.0.1:5433/reliabilitylab"
)


def server_url() -> str:
    return (
        os.environ.get("APP_TEST_DATABASE_URL")
        or os.environ.get("APP_DATABASE_URL")
        or LOCAL_DEFAULT_URL
    )


def with_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


class TemporaryDatabase:
    def __init__(self, server: str) -> None:
        self.name = f"reliabilitylab_test_{uuid.uuid4().hex[:12]}"
        self.url = with_database(server, self.name)
        self._admin = create_engine(server, isolation_level="AUTOCOMMIT")

    def create(self) -> None:
        with self._admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{self.name}"'))

    def drop(self) -> None:
        with self._admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        self._admin.dispose()


@pytest.fixture(scope="session")
def postgres_server() -> str:
    url = server_url()
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        safe = make_url(url).render_as_string(hide_password=True)
        message = f"PostgreSQL not reachable at {safe}: {type(exc).__name__}"
        if os.environ.get("APP_REQUIRE_POSTGRES") == "1":
            pytest.fail(message)
        pytest.skip(message)
    finally:
        engine.dispose()
    return url


@pytest.fixture
def temporary_database(postgres_server: str) -> Iterator[TemporaryDatabase]:
    """An empty, unmigrated database for migration tests."""
    database = TemporaryDatabase(postgres_server)
    database.create()
    try:
        yield database
    finally:
        database.drop()


@pytest.fixture(scope="session")
def migrated_engine(postgres_server: str) -> Iterator[Engine]:
    database = TemporaryDatabase(postgres_server)
    database.create()
    engine = create_engine(database.url, hide_parameters=True)
    try:
        command.upgrade(alembic_config(database.url), "head")
        yield engine
    finally:
        engine.dispose()
        database.drop()


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def sessions(connection: Connection) -> sessionmaker[Session]:
    return sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def session(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as session:
        yield session
