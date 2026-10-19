"""Background execution through real Redis and an in-process Celery worker."""

import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from app.db.models import RunRow
from tests.db.jobs.conftest import Harness

PLANNED = 12  # the evaluation suite has 12 scenarios; one repetition


def stored_runs(harness: Harness, experiment_id: str) -> list[str]:
    with harness.db.sessions() as session:
        return list(session.scalars(select(RunRow.id).where(RunRow.experiment_id == experiment_id)))


def test_create_returns_202_and_queues_in_redis_without_waiting(harness: Harness) -> None:
    started = time.monotonic()
    created = harness.create()
    elapsed = time.monotonic() - started

    assert created["status_code"] == 202
    assert created["status"] == "queued"
    assert created["status_url"] == f"/api/experiments/{created['experiment_id']}/status"
    assert harness.queue_length() == 1  # one real message in Redis, no worker yet
    status = harness.status(str(created["experiment_id"]))
    assert status["status"] == "queued" and status["terminal"] is False
    assert status["progress"] == {"planned_runs": PLANNED, "stored_runs": 0, "evaluated_runs": 0}
    assert status["poll_after_seconds"] == 2
    assert elapsed < 5  # did not run the benchmark


def test_worker_executes_a_queued_experiment(harness: Harness, worker: Callable[[], Any]) -> None:
    created = harness.create()
    experiment_id = str(created["experiment_id"])
    with worker():
        final = harness.wait_terminal(experiment_id)

    assert final["status"] == "completed"
    assert final["attempts"] == 1
    assert final["progress"] == {
        "planned_runs": PLANNED,
        "stored_runs": PLANNED,
        "evaluated_runs": PLANNED,
    }
    assert final["poll_after_seconds"] is None
    assert harness.queue_length() == 0
    detail = harness.client.get(f"/api/experiments/{experiment_id}").json()
    assert detail["metrics"]["task_success_rate"]["numerator"] == 11


def test_worker_unavailable_keeps_the_job_queued_until_one_starts(
    harness: Harness, worker: Callable[[], Any]
) -> None:
    experiment_id = str(harness.create()["experiment_id"])
    time.sleep(1.0)
    assert harness.status(experiment_id)["status"] == "queued"
    assert stored_runs(harness, experiment_id) == []
    with worker():
        assert harness.wait_terminal(experiment_id)["status"] == "completed"
    with harness.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == PLANNED
