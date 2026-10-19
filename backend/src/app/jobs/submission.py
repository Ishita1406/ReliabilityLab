"""Submitting experiments to the queue, cancelling them, and reporting their status.

Ordering on submit: the experiment is marked ``queued`` in PostgreSQL **first**, then the
message is published. A worker that receives the message therefore always finds the queued
experiment. If publishing fails (Redis unreachable), the experiment is reverted to ``pending``
and ``QueueUnavailableError`` is raised, so nothing is left queued without a message. (If the
process dies between commit and publish, the job stays ``queued``; recovery re-publishes it.)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from kombu.exceptions import KombuError, OperationalError
from redis.exceptions import RedisError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import jobs
from app.db.runs import load_experiment_configuration
from app.domain.enums import ExperimentStatus
from app.domain.timestamps import utc_now
from app.logs import log_at, log_event

logger = logging.getLogger("app.jobs.submission")


class QueueUnavailableError(RuntimeError):
    """The broker could not accept the message."""


def publish(experiment_id: str, settings: Settings) -> str:
    """Send the execution task; raises ``QueueUnavailableError`` if Redis is unreachable.

    Uses an explicit connection built from ``settings`` (not Celery's pooled default), so the
    configured broker URL and connect timeout always apply."""
    from app.jobs.celery_app import celery_app
    from app.jobs.tasks import execute_experiment

    try:
        with celery_app.connection_for_write(
            settings.redis_url.get_secret_value(),
            connect_timeout=settings.enqueue_timeout_seconds,
            transport_options={
                "socket_connect_timeout": settings.enqueue_timeout_seconds,
                "socket_timeout": max(settings.enqueue_timeout_seconds, 5.0),
                "visibility_timeout": settings.broker_visibility_timeout_seconds,
            },
        ) as connection:
            message = execute_experiment.apply_async(
                args=[experiment_id],
                connection=connection,
                retry=True,
                retry_policy={
                    "max_retries": 2,
                    "interval_start": 0,
                    "interval_step": 0.2,
                    "interval_max": 0.5,
                },
            )
    except (OperationalError, KombuError, RedisError, OSError) as exc:
        raise QueueUnavailableError(f"job queue unavailable: {type(exc).__name__}") from exc
    return str(message.id)


Publisher = Callable[[str, Settings], str]


@dataclass(frozen=True)
class SubmitResult:
    status: ExperimentStatus
    enqueued: bool


class SubmissionService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        settings: Settings,
        *,
        publisher: Publisher = publish,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._settings = settings
        self._publish = publisher
        self._now = now

    def submit(self, experiment_id: str) -> SubmitResult:
        """pending -> queued + publish. Idempotent: other statuses are returned unchanged."""
        with self._sessions.begin() as session:
            jobs.ensure_job(
                session,
                experiment_id,
                now=self._now(),
                max_attempts=self._settings.job_max_attempts,
            )
            queued = jobs.mark_queued(session, experiment_id, self._now())
            status = jobs.load_job(session, experiment_id).status
        if not queued:
            return SubmitResult(status, enqueued=False)
        try:
            task_id = self._publish(experiment_id, self._settings)
        except QueueUnavailableError as exc:
            with self._sessions.begin() as session:
                jobs.revert_to_pending(session, experiment_id, self._now())
            log_at(
                logger,
                logging.ERROR,
                "queue.unavailable",
                experiment_id=experiment_id,
                error=str(exc),
            )
            raise
        with self._sessions.begin() as session:
            jobs.record_task(session, experiment_id, task_id, self._now())
        log_event(logger, "job.submitted", experiment_id=experiment_id, task_id=task_id)
        return SubmitResult(ExperimentStatus.QUEUED, enqueued=True)

    def cancel(self, experiment_id: str) -> ExperimentStatus:
        with self._sessions.begin() as session:
            status = jobs.request_cancel(session, experiment_id, self._now())
        log_event(logger, "job.cancel_requested", experiment_id=experiment_id, status=status)
        return status

    def status(self, experiment_id: str) -> tuple[jobs.JobState, jobs.Progress]:
        with self._sessions() as session:
            state = jobs.load_job(session, experiment_id)
            configuration = load_experiment_configuration(session, experiment_id)
            planned = (
                len(configuration.scenario_ids) * configuration.repetitions if configuration else 0
            )
            return state, jobs.progress(session, experiment_id, planned)
