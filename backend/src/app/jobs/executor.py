"""Execute one attempt of an experiment job against PostgreSQL (no Celery or Redis here).

Used by the Celery task (``app.jobs.tasks``) and by the synchronous
``ExperimentService.run_experiment``, so both paths run the same domain logic:

1. **Claim** the execution lease (``app.db.jobs.claim``). Not queued, already finished, held by
   another live worker, or over the concurrency limit -> return without doing anything.
2. **Load and verify** the configuration snapshot, suite, evaluator, and agent implementation.
3. For every planned run, in order:
   * stop with ``cancelled`` if cancellation was requested (checked between runs only, so a run
     is never interrupted half-way);
   * **skip** runs that are already stored -- run IDs are deterministic, so a retried or
     redelivered job resumes instead of duplicating work;
   * execute the run, then in **one transaction** verify the lease is still ours, store the run
     with its trace and evaluation, and renew the lease. A worker that lost its lease cannot
     write.
4. Finish ``completed``.

Failures are classified: **transient** (database connectivity, timeouts) -> released back to
``queued`` while attempts remain (the caller schedules the retry), else ``failed``;
**permanent** (configuration errors, agent bugs, time limit exceeded) -> ``failed`` at once.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db import jobs
from app.db.benchmarks import load_agent_version, load_suite
from app.db.evaluations import save_run_evaluation
from app.db.runs import load_experiment, load_experiment_configuration, save_run
from app.domain.enums import ExperimentStatus
from app.domain.timestamps import utc_now
from app.evaluation.engine import Evaluator
from app.experiments.executor import (
    ExperimentConfigurationError,
    check_inputs,
    execute,
    planned_requests,
)
from app.jobs.registry import UnknownImplementationError, for_descriptor
from app.logs import log_at, log_event
from app.runner.runner import ScenarioRunner
from app.tracing.collector import TraceCollector
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy

logger = logging.getLogger("app.jobs")

MAX_ERROR_CHARS = 500


class JobOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY = "retry"
    BUSY = "busy"
    NOT_RUNNABLE = "not_runnable"
    LEASE_LOST = "lease_lost"


@dataclass
class JobResult:
    outcome: JobOutcome
    run_ids: list[str] = field(default_factory=list)
    """Runs stored by this attempt (in planned order)."""
    skipped: int = 0
    """Planned runs that were already stored (resumed after a retry or redelivery)."""
    error: Exception | None = None


@dataclass(frozen=True)
class JobSettings:
    lease_seconds: float = 120.0
    max_running: int = 4


def is_transient(exc: BaseException) -> bool:
    """Errors worth retrying: lost database connections and network timeouts."""
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, OperationalError | InterfaceError | ConnectionError | TimeoutError)


def _error(exc: BaseException) -> tuple[str, str]:
    return type(exc).__name__, (str(exc) or repr(exc))[:MAX_ERROR_CHARS]


class JobExecutor:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        settings: JobSettings | None = None,
        evaluator_factory: Callable[[], Evaluator] = Evaluator,
        now: Callable[[], datetime] = utc_now,
        policy: TracePayloadPolicy = DEFAULT_POLICY,
    ) -> None:
        self._sessions = sessions
        self._settings = settings or JobSettings()
        self._evaluator_factory = evaluator_factory
        self._now = now
        self._policy = policy
        self._collector = TraceCollector(policy)

    def execute(
        self,
        experiment_id: str,
        worker_id: str,
        *,
        runner: ScenarioRunner | None = None,
        evaluator: Evaluator | None = None,
    ) -> JobResult:
        """Run one attempt. ``runner``/``evaluator`` default to the registered implementation
        named in the snapshot and the configured evaluator (workers); callers may pass them
        explicitly (synchronous execution, tests)."""
        ids = {"experiment_id": experiment_id, "worker": worker_id}
        with self._sessions.begin() as session:
            claimed = jobs.claim(
                session,
                experiment_id,
                worker_id=worker_id,
                now=self._now(),
                lease_seconds=self._settings.lease_seconds,
                max_running=self._settings.max_running,
            )
            state = jobs.load_job(session, experiment_id)
        log_event(
            logger, f"job.{claimed.value}", attempt=state.attempts, status=state.status, **ids
        )
        if claimed is jobs.ClaimOutcome.BUSY:
            return JobResult(JobOutcome.BUSY)
        if claimed is jobs.ClaimOutcome.EXHAUSTED:
            return JobResult(JobOutcome.FAILED)
        if claimed is not jobs.ClaimOutcome.CLAIMED:
            return JobResult(JobOutcome.NOT_RUNNABLE)

        result = JobResult(JobOutcome.COMPLETED)
        try:
            self._run(experiment_id, worker_id, runner, evaluator, result, ids)
        except jobs.LeaseLostError as exc:
            log_at(logger, logging.WARNING, "job.lease_lost", error=str(exc), **ids)
            result.outcome, result.error = JobOutcome.LEASE_LOST, exc
        except Exception as exc:  # noqa: BLE001 - the job boundary: classify, record, report
            result.error = exc
            error = _error(exc)
            try:
                result.outcome = self._record_failure(experiment_id, worker_id, exc, error)
            except jobs.LeaseLostError as lost:
                log_at(logger, logging.WARNING, "job.lease_lost", error=str(lost), **ids)
                result.outcome = JobOutcome.LEASE_LOST
                return result
            log_at(
                logger,
                logging.WARNING if result.outcome is JobOutcome.RETRY else logging.ERROR,
                f"job.{result.outcome.value}",
                error_type=error[0],
                error=error[1],
                transient=is_transient(exc),
                stored_runs=len(result.run_ids),
                **ids,
            )
        return result

    def _record_failure(
        self, experiment_id: str, worker_id: str, exc: Exception, error: tuple[str, str]
    ) -> JobOutcome:
        with self._sessions.begin() as session:
            if is_transient(exc):
                status = jobs.release_for_retry(
                    session, experiment_id, worker_id, self._now(), error
                )
                return JobOutcome.RETRY if status is ExperimentStatus.QUEUED else JobOutcome.FAILED
            jobs.finish(
                session, experiment_id, worker_id, ExperimentStatus.FAILED, self._now(), error=error
            )
            return JobOutcome.FAILED

    def _run(
        self,
        experiment_id: str,
        worker_id: str,
        runner: ScenarioRunner | None,
        evaluator: Evaluator | None,
        result: JobResult,
        ids: dict[str, str],
    ) -> None:
        with self._sessions.begin() as session:
            experiment = load_experiment(session, experiment_id)
            configuration = load_experiment_configuration(session, experiment_id)
            if configuration is None:
                raise ExperimentConfigurationError("experiment has no configuration snapshot")
            suite = load_suite(session, experiment.suite_id, experiment.suite_version)
            if (
                load_agent_version(session, experiment.agent_version_id)
                != configuration.agent_version
            ):
                raise ExperimentConfigurationError("agent version changed since creation")
            existing = jobs.stored_run_ids(session, experiment_id)
        if runner is None:
            try:
                implementation = for_descriptor(configuration.agent_implementation)
            except UnknownImplementationError as exc:
                raise ExperimentConfigurationError(str(exc)) from exc
            # A fresh runner per job: every run still gets its own clock, tools, and fault
            # injector inside ScenarioRunner.run, so faults cannot leak between runs or jobs.
            runner = ScenarioRunner(implementation.factory)
        evaluator = evaluator or self._evaluator_factory()
        check_inputs(configuration, suite, evaluator)

        for request in planned_requests(experiment_id, configuration, suite):
            run_ids = {**ids, "run_id": request.run_id, "scenario_id": request.scenario.id}
            with self._sessions.begin() as session:
                cancel = jobs.cancel_requested(session, experiment_id)
                if cancel:
                    jobs.finish(
                        session, experiment_id, worker_id, ExperimentStatus.CANCELLED, self._now()
                    )
            if cancel:
                result.outcome = JobOutcome.CANCELLED
                log_event(logger, "job.cancelled", stored_runs=len(result.run_ids), **ids)
                return
            if request.run_id in existing:
                result.skipped += 1
                log_event(logger, "run.skipped", reason="already stored", **run_ids)
                continue
            executed = execute(request, configuration, runner, evaluator, self._collector)
            with self._sessions.begin() as session:
                jobs.assert_lease(session, experiment_id, worker_id)
                save_run(session, executed.result, executed.trace, self._policy)
                save_run_evaluation(session, experiment_id, executed.evaluation)
                jobs.renew_lease(
                    session, experiment_id, worker_id, self._now(), self._settings.lease_seconds
                )
            result.run_ids.append(request.run_id)
            log_event(
                logger,
                "run.completed",
                run_status=executed.result.run.status,
                verdict=executed.evaluation.verdict,
                trace_events=len(executed.trace.events),
                **run_ids,
            )

        with self._sessions.begin() as session:
            jobs.finish(session, experiment_id, worker_id, ExperimentStatus.COMPLETED, self._now())
        log_event(
            logger,
            "job.completed",
            stored_runs=len(result.run_ids),
            skipped_runs=result.skipped,
            **ids,
        )
