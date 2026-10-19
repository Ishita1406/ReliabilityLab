"""Retries, idempotency, failures, cancellation, limits, recovery, and logging."""

import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db import jobs
from app.db.models import ExperimentRow, RunRow, TraceEventRow
from app.domain.enums import ExperimentStatus
from app.evaluation.engine import Evaluator
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.executor import ExecutedRun, execute
from app.experiments.samples import EVALUATION_SUITE
from app.jobs.executor import JobExecutor, JobOutcome, JobSettings, is_transient
from app.jobs.recover import recover
from app.jobs.submission import publish
from app.jobs.tasks import retry_delay
from app.logs import JsonFormatter, clean_fields
from app.runner.runner import RunRequest, ScenarioRunner
from app.tracing.collector import TraceCollector
from tests.db.jobs.conftest import Harness
from tests.db.jobs.slow_agent import SLOW_VERSION
from tests.db.jobs.test_background_execution import PLANNED, stored_runs

PREFIX = "runner.lighthouse-year."


def on_execute(monkeypatch: pytest.MonkeyPatch, hook: Callable[[int], None]) -> list[int]:
    """Call ``hook(n)`` before the n-th executed run (1-based, across executors and attempts)."""
    calls: list[int] = []

    def wrapped(
        request: RunRequest,
        configuration: ExperimentConfiguration,
        runner: ScenarioRunner,
        evaluator: Evaluator,
        collector: TraceCollector,
    ) -> ExecutedRun:
        calls.append(len(calls) + 1)
        hook(len(calls))
        return execute(request, configuration, runner, evaluator, collector)

    monkeypatch.setattr("app.jobs.executor.execute", wrapped)
    return calls


def transient() -> OperationalError:
    return OperationalError("SELECT 1", None, ConnectionError("simulated connection loss"))


@pytest.fixture
def fail_execute(monkeypatch: pytest.MonkeyPatch) -> Callable[[set[int], Exception], list[int]]:
    """Make the n-th executed run (1-based, across attempts) raise ``error``."""

    def install(on_calls: set[int], error: Exception) -> list[int]:
        def hook(n: int) -> None:
            if n in on_calls:
                raise error

        return on_execute(monkeypatch, hook)

    return install


@pytest.fixture
def log_lines() -> Iterator[list[str]]:
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(JsonFormatter().format(record))

    handler = Collect()
    logger = logging.getLogger("app")
    logger.addHandler(handler)
    yield lines
    logger.removeHandler(handler)


def events(lines: list[str], name: str) -> list[dict[str, Any]]:
    parsed = [json.loads(line) for line in lines]
    return [p for p in parsed if p["event"] == name]


# --- idempotency -----------------------------------------------------------------------------


def test_idempotent_creation_replays_without_duplicates(harness: Harness) -> None:
    first = harness.create(key="create-1", repetitions=1)
    again = harness.create(key="create-1", repetitions=1)
    assert first["status_code"] == again["status_code"] == 202
    assert again["experiment_id"] == first["experiment_id"]
    assert (first["idempotent_replay"], again["idempotent_replay"]) == (False, True)
    assert harness.queue_length() == 1  # the replay did not publish a second message
    with harness.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentRow)) == 1

    conflict = harness.create(key="create-1", repetitions=2)
    assert conflict["status_code"] == 409
    assert "different request" in str(conflict["detail"])

    unkeyed = [harness.create()["experiment_id"] for _ in range(2)]
    assert len(set(unkeyed)) == 2  # without a key, every request creates an experiment


def test_queue_unavailable_returns_503_and_leaves_the_experiment_pending(harness: Harness) -> None:
    harness.reconfigure(redis_url="redis://127.0.0.1:1/0", enqueue_timeout_seconds=0.5)
    down = harness.create(key="while-down")
    assert down["status_code"] == 503
    assert down["status"] == "pending"
    experiment_id = str(down["experiment_id"])
    assert harness.status(experiment_id)["status"] == "pending"

    harness.reconfigure()  # Redis back
    retried = harness.create(key="while-down")  # same key: replays and submits now
    assert (retried["status_code"], retried["status"]) == (202, "queued")
    assert retried["experiment_id"] == experiment_id
    assert harness.queue_length() == 1


def test_submit_and_cancel_are_idempotent_by_state(harness: Harness) -> None:
    created = harness.create(submit=False)
    experiment_id = str(created["experiment_id"])
    assert created["status"] == "pending" and harness.queue_length() == 0
    first = harness.client.post(f"/api/experiments/{experiment_id}/submit")
    second = harness.client.post(f"/api/experiments/{experiment_id}/submit")
    assert (first.status_code, second.status_code) == (202, 200)
    assert harness.queue_length() == 1

    cancelled = harness.client.post(f"/api/experiments/{experiment_id}/cancel")
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled")
    again = harness.client.post(f"/api/experiments/{experiment_id}/submit")
    assert (again.status_code, again.json()["status"]) == (200, "cancelled")


# --- failures and retries --------------------------------------------------------------------


def test_permanent_failure_is_not_retried(
    harness: Harness, worker: Callable[[], Any], fail_execute: Callable[..., list[int]]
) -> None:
    calls = fail_execute({4}, RuntimeError("agent harness bug"))
    experiment_id = str(harness.create()["experiment_id"])
    with worker():
        final = harness.wait_terminal(experiment_id)
    assert final["status"] == "failed"
    assert (final["attempts"], final["last_error_type"]) == (1, "RuntimeError")
    assert final["last_error_message"] == "agent harness bug"
    assert len(calls) == 4
    assert len(stored_runs(harness, experiment_id)) == 3  # completed runs are kept


def test_transient_failure_is_retried_and_resumes_without_duplicates(
    harness: Harness,
    worker: Callable[[], Any],
    fail_execute: Callable[..., list[int]],
    log_lines: list[str],
) -> None:
    calls = fail_execute({4}, transient())
    experiment_id = str(harness.create()["experiment_id"])
    with worker():
        final = harness.wait_terminal(experiment_id)

    assert final["status"] == "completed"
    assert final["attempts"] == 2
    assert final["last_error_type"] == "OperationalError"  # kept for diagnosis
    runs = stored_runs(harness, experiment_id)
    assert len(runs) == len(set(runs)) == PLANNED
    # Attempt 1 ran 3 runs then failed on the 4th; attempt 2 skipped the 3 stored runs.
    assert len(calls) == 4 + (PLANNED - 3)
    skipped = [e for e in events(log_lines, "run.skipped") if e["experiment_id"] == experiment_id]
    assert len(skipped) == 3
    assert len(events(log_lines, "job.retry")) == 1


def test_retries_are_bounded_by_max_attempts(
    harness: Harness, worker: Callable[[], Any], fail_execute: Callable[..., list[int]]
) -> None:
    fail_execute(set(range(1, 100)), transient())
    experiment_id = str(harness.create()["experiment_id"])
    with worker():
        final = harness.wait_terminal(experiment_id)
    assert final["status"] == "failed"
    assert final["attempts"] == final["max_attempts"] == 3
    assert final["last_error_type"] == "OperationalError"
    assert stored_runs(harness, experiment_id) == []


def test_duplicate_deliveries_do_not_duplicate_runs(
    harness: Harness, worker: Callable[[], Any], log_lines: list[str]
) -> None:
    experiment_id = str(harness.create()["experiment_id"])
    publish(experiment_id, harness.settings)  # a duplicate message for the same job
    assert harness.queue_length() == 2
    with worker():
        final = harness.wait_terminal(experiment_id)
        harness.wait_queue_drained()
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        while not events(log_lines, "job.not_runnable") and datetime.now(UTC) < deadline:
            time.sleep(0.05)
    assert final["status"] == "completed" and final["attempts"] == 1
    assert len(stored_runs(harness, experiment_id)) == PLANNED
    assert len(events(log_lines, "job.not_runnable")) == 1


def test_soft_time_limit_fails_the_job_without_retry(
    harness: Harness, fail_execute: Callable[..., list[int]]
) -> None:
    fail_execute({2}, SoftTimeLimitExceeded())
    experiment_id = str(harness.create()["experiment_id"])
    result = JobExecutor(harness.db.sessions).execute(experiment_id, "w1")
    assert result.outcome is JobOutcome.FAILED
    final = harness.status(experiment_id)
    assert (final["status"], final["attempts"], final["last_error_type"]) == (
        "failed",
        1,
        "SoftTimeLimitExceeded",
    )


# --- leases and concurrency ------------------------------------------------------------------


def test_worker_that_lost_its_lease_cannot_write(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment_id = str(harness.create()["experiment_id"])
    t0 = datetime.now(UTC)
    slow = JobExecutor(harness.db.sessions, settings=JobSettings(lease_seconds=1), now=lambda: t0)
    takeover = JobExecutor(harness.db.sessions, now=lambda: t0 + timedelta(seconds=5))
    outcomes: list[JobOutcome] = []

    def stall_then_take_over(n: int) -> None:
        if n == 2:  # worker 1 stalls past its lease; worker 2 takes over and finishes the job
            outcomes.append(takeover.execute(experiment_id, "w2").outcome)

    on_execute(monkeypatch, stall_then_take_over)
    first = slow.execute(experiment_id, "w1")

    assert outcomes == [JobOutcome.COMPLETED]
    assert first.outcome is JobOutcome.LEASE_LOST
    # Worker 1 stored only its first run; its second (computed after losing the lease) was
    # refused, and worker 2 stored that run itself.
    assert len(first.run_ids) == 1
    runs = stored_runs(harness, experiment_id)
    assert len(runs) == len(set(runs)) == PLANNED
    final = harness.status(experiment_id)
    assert (final["status"], final["attempts"]) == ("completed", 2)


def test_stale_worker_cannot_add_runs_after_a_takeover_cancelled_the_job(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lease check (not just the runs primary key) stops a stale worker: here the run it
    computed was never stored by anyone else, so only the lease check can refuse it."""
    experiment_id = str(harness.create()["experiment_id"])
    t0 = datetime.now(UTC)
    slow = JobExecutor(harness.db.sessions, settings=JobSettings(lease_seconds=1), now=lambda: t0)
    takeover = JobExecutor(harness.db.sessions, now=lambda: t0 + timedelta(seconds=5))
    outcomes: list[JobOutcome] = []

    def stall_cancel_take_over(n: int) -> None:
        if n == 2:
            with harness.db.sessions.begin() as session:
                jobs.request_cancel(session, experiment_id, t0 + timedelta(seconds=4))
            outcomes.append(takeover.execute(experiment_id, "w2").outcome)

    on_execute(monkeypatch, stall_cancel_take_over)
    first = slow.execute(experiment_id, "w1")

    assert outcomes == [JobOutcome.CANCELLED]
    assert first.outcome is JobOutcome.LEASE_LOST
    assert len(stored_runs(harness, experiment_id)) == 1  # the stale run was not written
    assert harness.status(experiment_id)["status"] == "cancelled"


def test_concurrency_limit_defers_claims_until_capacity_frees(
    harness: Harness, worker: Callable[[], Any]
) -> None:
    harness.reconfigure(max_running_experiments=1)
    blocker = str(harness.create(submit=False)["experiment_id"])
    with harness.db.sessions.begin() as session:
        jobs.mark_queued(session, blocker, datetime.now(UTC))
        assert (
            jobs.claim(
                session,
                blocker,
                worker_id="elsewhere",
                now=datetime.now(UTC),
                lease_seconds=600,
                max_running=1,
            )
            is jobs.ClaimOutcome.CLAIMED
        )
    waiting = str(harness.create()["experiment_id"])
    with worker():
        harness.wait_for(waiting, lambda s: s["status"] == "queued", timeout=2)
        time.sleep(1.0)  # several busy retries happen meanwhile
        assert harness.status(waiting)["status"] == "queued"
        assert harness.status(waiting)["attempts"] == 0  # busy retries consume no attempts
        with harness.db.sessions.begin() as session:
            jobs.finish(
                session, blocker, "elsewhere", ExperimentStatus.CANCELLED, datetime.now(UTC)
            )
        final = harness.wait_terminal(waiting)
    assert (final["status"], final["attempts"]) == ("completed", 1)


# --- cancellation ----------------------------------------------------------------------------


def test_cancel_running_experiment_stops_between_runs(
    harness: Harness, worker: Callable[[], Any]
) -> None:
    experiment_id = str(harness.create(agent_version_id=SLOW_VERSION.id)["experiment_id"])
    with worker():
        harness.wait_for(experiment_id, lambda s: s["progress"]["stored_runs"] >= 2)  # type: ignore[index]
        response = harness.client.post(f"/api/experiments/{experiment_id}/cancel")
        assert response.status_code == 202 and response.json()["cancel_requested"] is True
        final = harness.wait_terminal(experiment_id)
    assert final["status"] == "cancelled"
    kept = final["progress"]["stored_runs"]  # type: ignore[index]
    assert 2 <= kept < PLANNED
    assert len(stored_runs(harness, experiment_id)) == kept  # nothing ran after cancelling


def test_cancelled_queued_job_never_runs(harness: Harness, worker: Callable[[], Any]) -> None:
    experiment_id = str(harness.create()["experiment_id"])
    assert harness.client.post(f"/api/experiments/{experiment_id}/cancel").json()["status"] == (
        "cancelled"
    )
    with worker():
        harness.wait_queue_drained()  # the worker consumed the message and did nothing
    assert harness.status(experiment_id)["status"] == "cancelled"
    assert harness.status(experiment_id)["attempts"] == 0
    assert stored_runs(harness, experiment_id) == []


# --- isolation, limits, recovery, logging ------------------------------------------------------


def test_fault_injection_stays_in_its_own_runs(harness: Harness, worker: Callable[[], Any]) -> None:
    faulted = str(
        harness.create(
            scenario_ids=[PREFIX + "fetch-prompt-injection", PREFIX + "search-timeout-always"]
        )["experiment_id"]
    )
    clean = str(harness.create(scenario_ids=[PREFIX + "baseline"])["experiment_id"])
    with worker():
        harness.wait_terminal(faulted)
        harness.wait_terminal(clean)

    def fault_events(experiment_id: str) -> int:
        with harness.db.sessions() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(TraceEventRow)
                    .where(
                        TraceEventRow.experiment_id == experiment_id,
                        TraceEventRow.kind == "fault_injected",
                    )
                )
                or 0
            )

    assert fault_events(faulted) > 0
    assert fault_events(clean) == 0
    with harness.db.sessions() as session:
        configs = session.scalars(
            select(RunRow.effective_configuration).where(RunRow.experiment_id == clean)
        )
        assert all(config["faults"] == [] for config in configs)


def test_per_run_limits_are_applied_and_bounded(
    harness: Harness, worker: Callable[[], Any]
) -> None:
    too_high = harness.create(limits={"max_tool_calls": 10_000})
    assert too_high["status_code"] == 422
    assert "max_tool_calls 10000 > 100" in str(too_high["detail"])
    assert harness.create(repetitions=1000)["status_code"] == 422

    tight = str(
        harness.create(limits={"max_tool_calls": 1}, scenario_ids=[PREFIX + "baseline"])[
            "experiment_id"
        ]
    )
    with worker():
        assert harness.wait_terminal(tight)["status"] == "completed"
    with harness.db.sessions() as session:
        statuses = list(session.scalars(select(RunRow.status).where(RunRow.experiment_id == tight)))
    assert statuses == ["limit_exceeded"]  # the per-run tool-call limit stopped the agent


def test_recovery_republishes_jobs_whose_messages_were_lost(
    harness: Harness, worker: Callable[[], Any]
) -> None:
    experiment_id = str(harness.create()["experiment_id"])
    harness.redis.flushdb()  # Redis restarted without persistence: the message is gone
    assert harness.queue_length() == 0
    harness.reconfigure(job_lease_seconds=0.0001)  # treat any queued job as stale
    assert recover(harness.db.sessions, harness.settings) == [experiment_id]
    assert harness.queue_length() == 1
    harness.reconfigure()
    with worker():
        assert harness.wait_terminal(experiment_id)["status"] == "completed"


def test_logs_carry_ids_but_no_secrets_or_payloads(
    harness: Harness, worker: Callable[[], Any], log_lines: list[str]
) -> None:
    experiment_id = str(
        harness.create(scenario_ids=[PREFIX + "baseline", PREFIX + "fetch-prompt-injection"])[
            "experiment_id"
        ]
    )
    with worker():
        harness.wait_terminal(experiment_id)
    completed = [
        e for e in events(log_lines, "run.completed") if e["experiment_id"] == experiment_id
    ]
    assert len(completed) == 2
    assert all(e["run_id"].startswith("run_") and e["scenario_id"] for e in completed)
    text = "\n".join(log_lines)
    password = harness.db.url.split(":")[2].split("@")[0]
    assert password and password not in text
    assert "completed in 1873" not in text  # document content
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" not in text  # injected tool payload


def test_log_field_cleaning_and_helpers() -> None:
    cleaned = clean_fields(
        {
            "api_key": "abc",
            "url": "postgresql://user:hunter2@db/x",
            "note": "x" * 500,
            "count": 3,
        }
    )
    assert cleaned["api_key"] == "[REDACTED]"
    assert "hunter2" not in str(cleaned["url"])
    assert len(str(cleaned["note"])) < 300
    assert cleaned["count"] == 3
    assert [retry_delay(a, 5.0) for a in (1, 2, 3)] == [5.0, 10.0, 20.0]
    assert is_transient(transient()) and not is_transient(RuntimeError("bug"))


def test_suite_used_by_job_tests_is_the_evaluation_suite() -> None:
    assert len(EVALUATION_SUITE.scenarios) == PLANNED
