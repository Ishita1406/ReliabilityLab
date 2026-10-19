"""Experiment creation and background execution API.

``POST /api/experiments`` creates an experiment and (by default) submits it, returning
**202 Accepted** with the experiment ID and a status URL; it never waits for runs. Clients poll
``GET /api/experiments/{id}/status``.

**Idempotency.** Send an ``Idempotency-Key`` header to make creation safe to retry: the same
key with the same body returns the original experiment (``idempotent_replay: true``, no new
experiment, no duplicate queue message); the same key with a different body is a 409. Without
a key, every request creates a new experiment. Submission and cancellation are idempotent by
state: submitting a queued or running experiment, or cancelling a finished one, changes nothing.

Unauthenticated, like the rest of the API (see ``app.api.deps``).
"""

import hashlib
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.exc import IntegrityError

from app.api.deps import ApiModel, Sessions
from app.config import Settings, get_settings
from app.db import jobs
from app.db.benchmarks import load_agent_version, load_suite
from app.db.dashboard import AgentVersionSummary, list_agent_versions
from app.domain.enums import ExperimentStatus
from app.experiments.service import ExperimentService
from app.jobs.registry import UnknownImplementationError, default_evaluator, for_version
from app.jobs.submission import QueueUnavailableError, SubmissionService
from app.runtime.limits import ExecutionLimits

router = APIRouter(prefix="/api", tags=["jobs"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
POLL_SECONDS = 2


def get_submissions(sessions: Sessions, settings: SettingsDep) -> SubmissionService:
    return SubmissionService(sessions, settings)


Submissions = Annotated[SubmissionService, Depends(get_submissions)]


class LimitsInput(ApiModel):
    max_steps: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=1)
    deadline_seconds: float | None = Field(default=None, gt=0)


class ExperimentCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    suite_id: str
    suite_version: str
    scenario_ids: list[str] | None = Field(default=None, description="null: every scenario")
    agent_version_id: str
    repetitions: int = Field(default=1, ge=1)
    seed: int = Field(default=0, ge=0)
    limits: LimitsInput | None = None
    submit: bool = True


class ExperimentAccepted(ApiModel):
    experiment_id: str
    status: ExperimentStatus
    status_url: str
    idempotent_replay: bool


class ProgressView(ApiModel):
    planned_runs: int
    stored_runs: int
    evaluated_runs: int


class JobStatusView(ApiModel):
    experiment_id: str
    status: ExperimentStatus
    terminal: bool
    attempts: int
    max_attempts: int
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None
    cancel_requested: bool
    last_error_type: str | None
    last_error_message: str | None
    progress: ProgressView
    poll_after_seconds: int | None = Field(
        description="Suggested polling interval; null when done."
    )


def _limits(requested: LimitsInput | None, settings: Settings) -> ExecutionLimits:
    requested = requested or LimitsInput()
    limits = ExecutionLimits(
        max_steps=requested.max_steps or settings.default_run_max_steps,
        max_tool_calls=requested.max_tool_calls or settings.default_run_max_tool_calls,
        deadline_seconds=requested.deadline_seconds or settings.default_run_deadline_seconds,
    )
    over = [
        f"{name} {value} > {cap}"
        for name, value, cap in (
            ("max_steps", limits.max_steps, settings.max_run_max_steps),
            ("max_tool_calls", limits.max_tool_calls, settings.max_run_max_tool_calls),
            ("deadline_seconds", limits.deadline_seconds, settings.max_run_deadline_seconds),
        )
        if value > cap
    ]
    if over:
        raise HTTPException(422, f"per-run limits exceed the server maximum: {'; '.join(over)}")
    return limits


def _accepted(
    response: Response, experiment_id: str, state: ExperimentStatus, replay: bool
) -> ExperimentAccepted:
    url = f"/api/experiments/{experiment_id}/status"
    response.headers["Location"] = url
    return ExperimentAccepted(
        experiment_id=experiment_id, status=state, status_url=url, idempotent_replay=replay
    )


def _queue_unavailable(experiment_id: str, exc: QueueUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "queue_unavailable",
            "detail": f"{exc}; the experiment was created but not queued. Retry with "
            f"POST /api/experiments/{experiment_id}/submit.",
            "experiment_id": experiment_id,
            "status": ExperimentStatus.PENDING,
        },
    )


@router.get("/agent-versions")
def get_agent_versions(sessions: Sessions) -> list[AgentVersionSummary]:
    with sessions() as session:
        return list_agent_versions(session)


@router.post(
    "/experiments",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentAccepted,
    responses={409: {"description": "Idempotency key reused"}, 503: {"description": "Queue down"}},
)
def create_experiment(
    body: ExperimentCreate,
    response: Response,
    sessions: Sessions,
    settings: SettingsDep,
    submissions: Submissions,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> ExperimentAccepted | JSONResponse:
    if body.repetitions > settings.max_repetitions:
        raise HTTPException(422, f"repetitions {body.repetitions} > {settings.max_repetitions}")
    limits = _limits(body.limits, settings)
    request_sha256 = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()

    def replay(experiment_id: str, stored_sha256: str) -> ExperimentAccepted | JSONResponse:
        if stored_sha256 != request_sha256:
            raise HTTPException(409, "Idempotency-Key was already used with a different request")
        if body.submit:
            try:
                result = submissions.submit(experiment_id)
            except QueueUnavailableError as exc:
                return _queue_unavailable(experiment_id, exc)
            return _accepted(response, experiment_id, result.status, replay=True)
        with sessions() as session:
            current = jobs.load_job(session, experiment_id).status
        return _accepted(response, experiment_id, current, replay=True)

    if idempotency_key:
        with sessions() as session:
            existing = jobs.find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return replay(*existing)

    with sessions() as session:
        version = load_agent_version(session, body.agent_version_id)
        suite = load_suite(session, body.suite_id, body.suite_version)
    try:
        implementation = for_version(version)
    except UnknownImplementationError as exc:
        raise HTTPException(422, str(exc)) from exc
    service = ExperimentService(sessions, max_attempts=settings.job_max_attempts)
    try:
        experiment = service.create_experiment(
            name=body.name,
            description=body.description,
            suite_id=body.suite_id,
            suite_version=body.suite_version,
            scenario_ids=body.scenario_ids or [s.id for s in suite.scenarios],
            agent_version_id=body.agent_version_id,
            repetitions=body.repetitions,
            seed=body.seed,
            limits=limits,
            evaluator_version=default_evaluator(settings).version,
            agent_implementation=implementation.descriptor,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256 if idempotency_key else None,
        )
    except IntegrityError:
        # A concurrent request with the same key won the race: replay its result.
        if not idempotency_key:
            raise
        with sessions() as session:
            existing = jobs.find_by_idempotency_key(session, idempotency_key)
        if existing is None:
            raise
        return replay(*existing)

    if not body.submit:
        return _accepted(response, experiment.id, ExperimentStatus.PENDING, replay=False)
    try:
        result = submissions.submit(experiment.id)
    except QueueUnavailableError as exc:
        return _queue_unavailable(experiment.id, exc)
    return _accepted(response, experiment.id, result.status, replay=False)


@router.post(
    "/experiments/{experiment_id}/submit",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentAccepted,
)
def submit_experiment(
    experiment_id: str, response: Response, submissions: Submissions
) -> ExperimentAccepted | JSONResponse:
    try:
        result = submissions.submit(experiment_id)
    except QueueUnavailableError as exc:
        return _queue_unavailable(experiment_id, exc)
    if not result.enqueued:
        response.status_code = status.HTTP_200_OK
    return _accepted(response, experiment_id, result.status, replay=not result.enqueued)


@router.post("/experiments/{experiment_id}/cancel")
def cancel_experiment(
    experiment_id: str, response: Response, submissions: Submissions
) -> JobStatusView:
    result = submissions.cancel(experiment_id)
    if result is ExperimentStatus.RUNNING:
        response.status_code = status.HTTP_202_ACCEPTED  # stops after the current run
    return get_status(experiment_id, submissions)


@router.get("/experiments/{experiment_id}/status")
def get_status(experiment_id: str, submissions: Submissions) -> JobStatusView:
    state, progress = submissions.status(experiment_id)
    terminal = state.status in jobs.TERMINAL
    return JobStatusView(
        experiment_id=experiment_id,
        status=state.status,
        terminal=terminal,
        attempts=state.attempts,
        max_attempts=state.max_attempts,
        queued_at=state.queued_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        heartbeat_at=state.heartbeat_at,
        lease_expires_at=state.lease_expires_at,
        cancel_requested=state.cancel_requested_at is not None,
        last_error_type=state.last_error_type,
        last_error_message=state.last_error_message,
        progress=ProgressView(
            planned_runs=progress.planned_runs,
            stored_runs=progress.stored_runs,
            evaluated_runs=progress.evaluated_runs,
        ),
        poll_after_seconds=None if terminal else POLL_SECONDS,
    )
