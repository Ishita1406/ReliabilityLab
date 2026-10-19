"""Celery tasks. Each task is a thin wrapper around ``JobExecutor.execute``.

Retry policy (all durable state is in PostgreSQL):

* transient failure, attempts left -> the job is back in ``queued``; the task re-schedules
  itself with exponential backoff (``job_retry_backoff_seconds * 2**(attempt-1)``);
* concurrency limit reached (``busy``) -> re-scheduled after ``job_busy_retry_seconds``; this
  does not consume an attempt;
* permanent failure, completed, cancelled, nothing to do, or lease lost -> the message is
  acknowledged and nothing is retried.
"""

import logging
import os

from celery import Task
from celery.signals import worker_process_init, worker_ready

from app.db.jobs import load_job
from app.jobs.celery_app import celery_app
from app.jobs.executor import JobOutcome
from app.jobs.runtime import get_runtime
from app.logs import configure_logging, log_at, log_event

logger = logging.getLogger("app.jobs.tasks")


def retry_delay(attempt: int, base_seconds: float) -> float:
    return base_seconds * float(2 ** max(attempt - 1, 0))


@celery_app.task(bind=True, name="experiments.execute", max_retries=None)
def execute_experiment(self: Task, experiment_id: str) -> str:
    runtime = get_runtime()
    worker_id = f"{self.request.hostname or 'worker'}/{os.getpid()}"
    result = runtime.executor().execute(experiment_id, worker_id)
    settings = runtime.settings
    if result.outcome is JobOutcome.RETRY:
        with runtime.sessions() as session:
            attempt = load_job(session, experiment_id).attempts
        delay = retry_delay(attempt, settings.job_retry_backoff_seconds)
        log_at(
            logger,
            logging.WARNING,
            "task.retry_scheduled",
            experiment_id=experiment_id,
            delay_seconds=delay,
        )
        raise self.retry(countdown=delay)
    if result.outcome is JobOutcome.BUSY:
        log_event(
            logger,
            "task.busy_retry_scheduled",
            experiment_id=experiment_id,
            delay_seconds=settings.job_busy_retry_seconds,
        )
        raise self.retry(countdown=settings.job_busy_retry_seconds)
    return result.outcome.value


@worker_process_init.connect
def _configure_pool_process_logging(**_: object) -> None:
    """Tasks run in forked pool processes; each needs the JSON log handler."""
    configure_logging()


@worker_ready.connect
def _recover_on_start(**_: object) -> None:
    """Re-enqueue jobs whose messages may have been lost (Redis restart, crashed worker)."""
    configure_logging()
    from app.jobs.recover import recover

    runtime = get_runtime()
    try:
        recovered = recover(runtime.sessions, runtime.settings)
    except Exception as exc:  # noqa: BLE001 - recovery must not stop the worker from starting
        log_at(logger, logging.ERROR, "worker.recovery_failed", error_type=type(exc).__name__)
        return
    log_event(logger, "worker.recovered_jobs", count=len(recovered))
