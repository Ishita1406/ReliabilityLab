"""Experiments and the individual runs they consist of.

An ``Experiment`` evaluates one ``AgentVersion`` against selected scenarios of one exact
benchmark suite version, repeating each scenario ``repetitions`` times. Each (scenario,
repetition) pair is one ``Run``.
"""

from datetime import datetime
from typing import Self

from pydantic import Field, NonNegativeInt, model_validator

from app.domain.base import DomainModel, NonEmptyStr, SemVer, require_unique
from app.domain.enums import ExperimentStatus, RunStatus
from app.domain.ids import AgentVersionId, ExperimentId, IdPrefix, RunId, Slug, new_id
from app.domain.timestamps import UtcDateTime, utc_now


def _check_lifecycle(
    *,
    status: str,
    created_at: datetime,
    started_at: datetime | None,
    finished_at: datetime | None,
    not_started: set[str],
    in_progress: set[str],
    may_skip_start: set[str],
) -> None:
    """Enforce that timestamps are present exactly when the status implies them, in order."""
    if status in not_started:
        if started_at is not None or finished_at is not None:
            raise ValueError(f"status '{status}' must not have started or finished timestamps")
    elif status in in_progress:
        if started_at is None:
            raise ValueError(f"status '{status}' requires a start timestamp")
        if finished_at is not None:
            raise ValueError(f"status '{status}' must not have a finish timestamp")
    else:  # terminal
        if finished_at is None:
            raise ValueError(f"status '{status}' requires a finish timestamp")
        if started_at is None and status not in may_skip_start:
            raise ValueError(f"status '{status}' requires a start timestamp")

    ordered = [t for t in (created_at, started_at, finished_at) if t is not None]
    if ordered != sorted(ordered):
        raise ValueError("timestamps must satisfy created <= started <= finished")


class Experiment(DomainModel):
    id: ExperimentId = Field(default_factory=lambda: new_id(IdPrefix.EXPERIMENT))
    name: NonEmptyStr
    description: NonEmptyStr | None = None
    suite_id: Slug
    suite_version: SemVer
    agent_version_id: AgentVersionId
    scenario_ids: list[Slug] = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1, le=1000)
    seed: NonNegativeInt = Field(description="Base seed; runs derive their seeds from it.")
    status: ExperimentStatus = ExperimentStatus.PENDING
    created_at: UtcDateTime = Field(default_factory=utc_now)
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique(self.scenario_ids, "scenario_ids")
        _check_lifecycle(
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            not_started={ExperimentStatus.PENDING},
            in_progress={ExperimentStatus.RUNNING},
            may_skip_start={ExperimentStatus.CANCELLED},
        )
        return self


class RunError(DomainModel):
    """Why the harness could not complete a run (not an agent task failure)."""

    error_type: NonEmptyStr
    message: NonEmptyStr


class Run(DomainModel):
    id: RunId = Field(default_factory=lambda: new_id(IdPrefix.RUN))
    experiment_id: ExperimentId
    case_id: Slug
    scenario_id: Slug
    agent_version_id: AgentVersionId
    repetition: NonNegativeInt = Field(description="0-based repetition index.")
    seed: NonNegativeInt
    status: RunStatus = RunStatus.QUEUED
    created_at: UtcDateTime = Field(default_factory=utc_now)
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_lifecycle(
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            not_started={RunStatus.QUEUED},
            in_progress={RunStatus.RUNNING},
            may_skip_start={RunStatus.CANCELLED},
        )
        if self.status is RunStatus.ERRORED and self.error is None:
            raise ValueError("errored runs must include error details")
        if self.error is not None and self.status not in {
            RunStatus.ERRORED,
            RunStatus.TIMED_OUT,
            RunStatus.LIMIT_EXCEEDED,
        }:
            raise ValueError(f"status '{self.status}' must not include error details")
        return self
