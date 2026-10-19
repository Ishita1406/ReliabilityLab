"""Fixtures for background-execution tests against real Redis and real PostgreSQL.

Redis: ``APP_TEST_REDIS_URL`` (default: database 15 of the compose Redis on :6380), flushed
before every test. If Redis is unreachable these tests are skipped with the reason
(``APP_REQUIRE_REDIS=1`` makes that a failure). Each test gets its own migrated PostgreSQL
database with committed transactions, so worker threads and processes see real data.
"""

import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
import redis
from alembic import command
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_sessions
from app.config import Settings, get_settings
from app.domain.agent import AgentConfig
from app.experiments.samples import BASELINE_VERSION, EVALUATION_SUITE, VARIANT_VERSIONS
from app.experiments.service import ExperimentService
from app.jobs.celery_app import QUEUE, celery_app, configure
from app.jobs.runtime import Runtime, use_runtime
from app.main import create_app
from tests.db.conftest import TemporaryDatabase, alembic_config
from tests.db.jobs.slow_agent import SLOW_VERSION

TEST_REDIS_URL = os.environ.get("APP_TEST_REDIS_URL", "redis://127.0.0.1:6380/15")
CONFIG = AgentConfig(
    id=BASELINE_VERSION.agent_config_id,
    name="deterministic-research",
    created_at=BASELINE_VERSION.created_at,
)


@pytest.fixture(scope="session")
def redis_url() -> str:
    client = redis.Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=1)
    try:
        client.ping()
    except redis.RedisError as exc:
        message = f"Redis not reachable at {TEST_REDIS_URL}: {type(exc).__name__}"
        if os.environ.get("APP_REQUIRE_REDIS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    finally:
        client.close()
    return TEST_REDIS_URL


@pytest.fixture
def redis_client(redis_url: str) -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(redis_url)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@dataclass
class JobDatabase:
    url: str
    engine: Engine
    sessions: sessionmaker[Session]


@pytest.fixture
def job_db(postgres_server: str) -> Iterator[JobDatabase]:
    database = TemporaryDatabase(postgres_server)
    database.create()
    command.upgrade(alembic_config(database.url), "head")
    engine = create_engine(database.url, hide_parameters=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    service = ExperimentService(sessions)
    for version in (BASELINE_VERSION, VARIANT_VERSIONS["no-retry"], SLOW_VERSION):
        service.register(EVALUATION_SUITE, CONFIG, version)
    try:
        yield JobDatabase(database.url, engine, sessions)
    finally:
        engine.dispose()
        database.drop()


def make_settings(db: JobDatabase, redis_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": db.url,
        "redis_url": redis_url,
        "job_lease_seconds": 30.0,
        "job_retry_backoff_seconds": 0.0,
        "job_busy_retry_seconds": 0.2,
        "job_max_attempts": 3,
        "max_running_experiments": 4,
        "enqueue_timeout_seconds": 1.0,
        "agent_plugins": "tests.db.jobs.slow_agent",
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@dataclass
class Harness:
    db: JobDatabase
    settings: Settings
    client: TestClient
    redis: redis.Redis
    default_redis_url: str

    def reconfigure(self, **overrides: object) -> None:
        """Rebuild settings from the test defaults plus ``overrides`` (and apply everywhere)."""
        redis_url = str(overrides.pop("redis_url", self.default_redis_url))
        self.settings = make_settings(self.db, redis_url, **overrides)
        configure(celery_app, self.settings)
        use_runtime(Runtime(self.settings, self.db.sessions))
        self.client.app.dependency_overrides[get_settings] = lambda: self.settings  # type: ignore[attr-defined]

    def queue_length(self) -> int:
        length = self.redis.llen(QUEUE)
        assert isinstance(length, int)  # synchronous client
        return length

    def wait_queue_drained(self, timeout: float = 10) -> None:
        """Wait until the worker has taken every message from the Redis queue."""
        deadline = time.monotonic() + timeout
        while self.queue_length():
            if time.monotonic() > deadline:
                raise AssertionError(f"{self.queue_length()} message(s) still queued")
            time.sleep(0.05)

    def create(self, *, key: str | None = None, **body: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": "job test",
            "suite_id": EVALUATION_SUITE.id,
            "suite_version": EVALUATION_SUITE.version,
            "agent_version_id": BASELINE_VERSION.id,
            **body,
        }
        headers = {"Idempotency-Key": key} if key else {}
        response = self.client.post("/api/experiments", json=payload, headers=headers)
        return {"status_code": response.status_code, **response.json()}

    def status(self, experiment_id: str) -> dict[str, object]:
        response = self.client.get(f"/api/experiments/{experiment_id}/status")
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def wait_for(
        self,
        experiment_id: str,
        predicate: Callable[[dict[str, object]], bool],
        timeout: float = 30,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            current = self.status(experiment_id)
            if predicate(current):
                return current
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting; last status: {current}")
            time.sleep(0.1)

    def wait_terminal(self, experiment_id: str, timeout: float = 30) -> dict[str, object]:
        return self.wait_for(experiment_id, lambda s: bool(s["terminal"]), timeout)


@pytest.fixture
def harness(job_db: JobDatabase, redis_client: redis.Redis, redis_url: str) -> Iterator[Harness]:
    settings = make_settings(job_db, redis_url)
    configure(celery_app, settings)
    use_runtime(Runtime(settings, job_db.sessions))
    app = create_app()
    app.dependency_overrides[get_sessions] = lambda: job_db.sessions
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield Harness(job_db, settings, client, redis_client, redis_url)
    use_runtime(None)
    configure(celery_app, get_settings())


@pytest.fixture
def worker(harness: Harness) -> Callable[[], object]:
    """Context-manager factory for an in-process Celery worker (solo pool) on the real broker."""

    def start() -> object:
        return start_worker(
            celery_app,
            pool="solo",
            concurrency=1,
            perform_ping_check=False,
            loglevel="WARNING",
            shutdown_timeout=15,
        )

    return start
