"""Durable job state transitions (PostgreSQL). Redis is never consulted here.

The experiment's ``status`` is the state machine; ``experiment_jobs`` holds the lease and
bookkeeping. Every transition is a guarded update inside the caller's transaction:

    pending --submit--> queued --claim--> running --finish--> completed | failed | cancelled
                          ^                  |
                          +--release (retry)-+           (running with an expired lease can be
    pending | queued --cancel--> cancelled                claimed again: worker takeover)
    running --cancel--> cancel_requested (the worker stops between runs)

Timestamps come from the caller's clock (``now``) so experiment timestamps stay on one time
source and tests can control lease expiry.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.benchmarks import NotFoundError
from app.db.models import ExperimentJobRow, ExperimentRow, RunEvaluationRow, RunRow
from app.domain.enums import ExperimentStatus

CLAIM_LOCK = "reliabilitylab.experiment-claims"
TERMINAL = frozenset(
    {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED}
)


class LeaseLostError(RuntimeError):
    """This worker no longer holds the experiment's execution lease; it must stop writing."""


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    BUSY = "busy"
    """The global running-experiment limit is reached; try again later."""
    NOT_RUNNABLE = "not_runnable"
    """Not queued, already finished, or running under another worker's valid lease."""
    EXHAUSTED = "exhausted"
    """A lost worker used up the last attempt; the experiment was marked failed."""


@dataclass(frozen=True)
class JobState:
    experiment_id: str
    status: ExperimentStatus
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    last_error_type: str | None
    last_error_message: str | None
    celery_task_id: str | None


@dataclass(frozen=True)
class Progress:
    planned_runs: int
    stored_runs: int
    evaluated_runs: int


def ensure_job(
    session: Session,
    experiment_id: str,
    *,
    now: datetime,
    max_attempts: int,
    idempotency_key: str | None = None,
    request_sha256: str | None = None,
) -> None:
    """Create the job row if missing (experiments created before Milestone 10 have none)."""
    session.execute(
        insert(ExperimentJobRow)
        .values(
            experiment_id=experiment_id,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["experiment_id"])
    )


def find_by_idempotency_key(session: Session, key: str) -> tuple[str, str] | None:
    """(experiment_id, request_sha256) of an earlier creation request with this key."""
    row = session.execute(
        select(ExperimentJobRow.experiment_id, ExperimentJobRow.request_sha256).where(
            ExperimentJobRow.idempotency_key == key
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1] or "")


def load_job(session: Session, experiment_id: str, *, for_update: bool = False) -> JobState:
    query = (
        select(ExperimentRow, ExperimentJobRow)
        .join(ExperimentJobRow, ExperimentJobRow.experiment_id == ExperimentRow.id)
        .where(ExperimentRow.id == experiment_id)
    )
    if for_update:
        query = query.with_for_update()
    row = session.execute(query).one_or_none()
    if row is None:
        raise NotFoundError(f"no job for experiment '{experiment_id}'")
    experiment, job = row
    return JobState(
        experiment_id=experiment.id,
        status=experiment.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        lease_owner=job.lease_owner,
        lease_expires_at=job.lease_expires_at,
        heartbeat_at=job.heartbeat_at,
        cancel_requested_at=job.cancel_requested_at,
        queued_at=job.queued_at,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
        last_error_type=job.last_error_type,
        last_error_message=job.last_error_message,
        celery_task_id=job.celery_task_id,
    )


def _set_experiment(session: Session, experiment_id: str, **values: object) -> None:
    session.execute(update(ExperimentRow).where(ExperimentRow.id == experiment_id).values(**values))


def _set_job(session: Session, experiment_id: str, now: datetime, **values: object) -> None:
    session.execute(
        update(ExperimentJobRow)
        .where(ExperimentJobRow.experiment_id == experiment_id)
        .values(updated_at=now, **values)
    )


def mark_queued(session: Session, experiment_id: str, now: datetime) -> bool:
    """pending -> queued. False if the experiment was not pending (nothing changed)."""
    state = load_job(session, experiment_id, for_update=True)
    if state.status is not ExperimentStatus.PENDING:
        return False
    _set_experiment(session, experiment_id, status=ExperimentStatus.QUEUED)
    _set_job(session, experiment_id, now, queued_at=now)
    return True


def revert_to_pending(session: Session, experiment_id: str, now: datetime) -> bool:
    """queued -> pending when the queue message could not be published (never claimed)."""
    state = load_job(session, experiment_id, for_update=True)
    if state.status is not ExperimentStatus.QUEUED or state.started_at is not None:
        return False
    _set_experiment(session, experiment_id, status=ExperimentStatus.PENDING)
    _set_job(session, experiment_id, now, queued_at=None)
    return True


def record_task(session: Session, experiment_id: str, task_id: str, now: datetime) -> None:
    _set_job(session, experiment_id, now, celery_task_id=task_id)


def claim(
    session: Session,
    experiment_id: str,
    *,
    worker_id: str,
    now: datetime,
    lease_seconds: float,
    max_running: int,
) -> ClaimOutcome:
    """Take the execution lease of a queued experiment, or of a running one whose lease expired.

    Claims are serialised with a transaction-scoped advisory lock so the global running limit
    cannot be exceeded by concurrent claims."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(CLAIM_LOCK))))
    state = load_job(session, experiment_id, for_update=True)
    stale = (
        state.status is ExperimentStatus.RUNNING
        and state.lease_expires_at is not None
        and state.lease_expires_at <= now
    )
    if not (state.status is ExperimentStatus.QUEUED or stale):
        return ClaimOutcome.NOT_RUNNABLE
    if state.attempts >= state.max_attempts:
        _set_experiment(
            session,
            experiment_id,
            status=ExperimentStatus.FAILED,
            started_at=state.started_at or now,
            finished_at=now,
        )
        _set_job(
            session,
            experiment_id,
            now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_type="AttemptsExhausted",
            last_error_message=f"all {state.max_attempts} attempts used "
            + ("(the last worker was lost)" if stale else ""),
        )
        return ClaimOutcome.EXHAUSTED
    running = session.scalar(
        select(func.count())
        .select_from(ExperimentJobRow)
        .join(ExperimentRow, ExperimentRow.id == ExperimentJobRow.experiment_id)
        .where(
            ExperimentRow.status == ExperimentStatus.RUNNING,
            ExperimentJobRow.lease_expires_at > now,
            ExperimentRow.id != experiment_id,
        )
    )
    if (running or 0) >= max_running:
        return ClaimOutcome.BUSY
    _set_experiment(
        session,
        experiment_id,
        status=ExperimentStatus.RUNNING,
        started_at=state.started_at or now,
    )
    _set_job(
        session,
        experiment_id,
        now,
        attempts=state.attempts + 1,
        lease_owner=worker_id,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        heartbeat_at=now,
    )
    return ClaimOutcome.CLAIMED


def assert_lease(session: Session, experiment_id: str, worker_id: str) -> JobState:
    """Lock the job row and fail unless this worker still holds the lease of a running job."""
    state = load_job(session, experiment_id, for_update=True)
    if state.status is not ExperimentStatus.RUNNING or state.lease_owner != worker_id:
        raise LeaseLostError(
            f"worker '{worker_id}' lost the lease of experiment '{experiment_id}' "
            f"(status {state.status}, owner {state.lease_owner})"
        )
    return state


def renew_lease(
    session: Session, experiment_id: str, worker_id: str, now: datetime, lease_seconds: float
) -> None:
    assert_lease(session, experiment_id, worker_id)
    _set_job(
        session,
        experiment_id,
        now,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        heartbeat_at=now,
    )


def finish(
    session: Session,
    experiment_id: str,
    worker_id: str,
    status: ExperimentStatus,
    now: datetime,
    *,
    error: tuple[str, str] | None = None,
) -> None:
    """running -> completed | failed | cancelled, only by the lease holder."""
    if status not in TERMINAL:
        raise ValueError(f"'{status}' is not a terminal status")
    assert_lease(session, experiment_id, worker_id)
    _set_experiment(session, experiment_id, status=status, finished_at=now)
    values: dict[str, object] = {"lease_owner": None, "lease_expires_at": None}
    if error is not None:
        values |= {"last_error_type": error[0], "last_error_message": error[1]}
    _set_job(session, experiment_id, now, **values)


def release_for_retry(
    session: Session, experiment_id: str, worker_id: str, now: datetime, error: tuple[str, str]
) -> ExperimentStatus:
    """After a transient failure: back to queued if attempts remain, otherwise failed."""
    state = assert_lease(session, experiment_id, worker_id)
    common = {
        "lease_owner": None,
        "lease_expires_at": None,
        "last_error_type": error[0],
        "last_error_message": error[1],
    }
    if state.attempts < state.max_attempts:
        _set_experiment(session, experiment_id, status=ExperimentStatus.QUEUED)
        _set_job(session, experiment_id, now, queued_at=now, **common)
        return ExperimentStatus.QUEUED
    _set_experiment(session, experiment_id, status=ExperimentStatus.FAILED, finished_at=now)
    _set_job(session, experiment_id, now, **common)
    return ExperimentStatus.FAILED


def request_cancel(session: Session, experiment_id: str, now: datetime) -> ExperimentStatus:
    """Cancel now if not started by a worker; otherwise ask the worker to stop between runs.

    Returns the resulting status (``running`` means cancellation was requested)."""
    state = load_job(session, experiment_id, for_update=True)
    if state.status in TERMINAL:
        return state.status
    if state.status in (ExperimentStatus.PENDING, ExperimentStatus.QUEUED):
        _set_experiment(session, experiment_id, status=ExperimentStatus.CANCELLED, finished_at=now)
        _set_job(session, experiment_id, now, cancel_requested_at=now)
        return ExperimentStatus.CANCELLED
    _set_job(session, experiment_id, now, cancel_requested_at=now)
    return ExperimentStatus.RUNNING


def cancel_requested(session: Session, experiment_id: str) -> bool:
    return (
        session.scalar(
            select(ExperimentJobRow.cancel_requested_at.is_not(None)).where(
                ExperimentJobRow.experiment_id == experiment_id
            )
        )
        is True
    )


def recoverable(session: Session, now: datetime, queued_grace_seconds: float) -> list[str]:
    """Experiments whose queue message may be lost: queued for longer than the grace period,
    or running under an expired lease (crashed worker)."""
    cutoff = now - timedelta(seconds=queued_grace_seconds)
    rows = session.execute(
        select(ExperimentRow.id)
        .join(ExperimentJobRow, ExperimentJobRow.experiment_id == ExperimentRow.id)
        .where(
            (
                (ExperimentRow.status == ExperimentStatus.QUEUED)
                & (ExperimentJobRow.queued_at <= cutoff)
            )
            | (
                (ExperimentRow.status == ExperimentStatus.RUNNING)
                & (ExperimentJobRow.lease_expires_at <= now)
            )
        )
        .order_by(ExperimentRow.created_at)
    ).all()
    return [row[0] for row in rows]


def stored_run_ids(session: Session, experiment_id: str) -> set[str]:
    return set(session.scalars(select(RunRow.id).where(RunRow.experiment_id == experiment_id)))


def progress(session: Session, experiment_id: str, planned_runs: int) -> Progress:
    stored = session.scalar(
        select(func.count()).select_from(RunRow).where(RunRow.experiment_id == experiment_id)
    )
    evaluated = session.scalar(
        select(func.count())
        .select_from(RunEvaluationRow)
        .join(ExperimentRow, ExperimentRow.id == RunEvaluationRow.experiment_id)
        .where(
            RunEvaluationRow.experiment_id == experiment_id,
            RunEvaluationRow.evaluator_version == ExperimentRow.evaluator_version,
        )
    )
    return Progress(
        planned_runs=planned_runs, stored_runs=stored or 0, evaluated_runs=evaluated or 0
    )
