"""Review API: queue, calibration report, review history, submission, and corrections.

Unauthenticated (see ``app.api.deps``): ``reviewer`` is a self-declared name, not an identity.
Reviews submitted here are always ``provenance="human"``.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import ApiModel, Sessions
from app.db.benchmarks import NotFoundError
from app.db.human_reviews import list_human_reviews
from app.db.runs import load_experiment_configuration, load_run
from app.domain.base import NonEmptyStr
from app.evaluation.models import FailureLabel
from app.review.agreement import CalibrationReport
from app.review.models import (
    CategoryVerdict,
    HumanReview,
    ReviewEvidence,
    ReviewLabel,
    current_reviews,
)
from app.review.queue import QueueItem
from app.review.service import ReviewService

router = APIRouter(prefix="/api", tags=["reviews"])


@router.get("/experiments/{experiment_id}/review-queue")
def get_review_queue(experiment_id: str, sessions: Sessions) -> list[QueueItem]:
    return ReviewService(sessions).queue(experiment_id)


@router.get("/experiments/{experiment_id}/calibration")
def get_calibration(
    experiment_id: str,
    sessions: Sessions,
    scope: Annotated[Literal["sampled", "all"], Query()] = "sampled",
) -> CalibrationReport:
    return ReviewService(sessions).report(experiment_id, scope=scope)


class RunReviews(ApiModel):
    run_id: str
    evaluator_version: str | None
    records: list[HumanReview] = Field(description="Every record, oldest first.")
    current: list[HumanReview] = Field(description="Each reviewer's latest record.")


@router.get("/runs/{run_id}/reviews")
def get_run_reviews(run_id: str, sessions: Sessions) -> RunReviews:
    with sessions() as session:
        stored = load_run(session, run_id)
        configuration = load_experiment_configuration(session, stored.run.experiment_id)
        records = list_human_reviews(session, run_id=run_id)
    return RunReviews(
        run_id=run_id,
        evaluator_version=configuration.evaluator_version if configuration else None,
        records=records,
        current=current_reviews(records),
    )


class ReviewInput(ApiModel):
    label: ReviewLabel
    categories: dict[FailureLabel, CategoryVerdict] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    rationale: NonEmptyStr | None = None


class ReviewSubmission(ReviewInput):
    reviewer: NonEmptyStr
    blind: bool


class ReviewCorrection(ReviewInput):
    reason: NonEmptyStr


@router.post("/runs/{run_id}/reviews", status_code=status.HTTP_201_CREATED)
def submit_review(run_id: str, body: ReviewSubmission, sessions: Sessions) -> HumanReview:
    with sessions() as session:
        stored = load_run(session, run_id)
        configuration = load_experiment_configuration(session, stored.run.experiment_id)
    if configuration is None:
        raise NotFoundError(f"run '{run_id}' belongs to an experiment without an evaluator")
    review = HumanReview(
        run_id=run_id,
        case_id=stored.run.case_id,
        scenario_id=stored.run.scenario_id,
        evaluator_version=configuration.evaluator_version,
        provenance="human",
        **body.model_dump(),
    )
    return ReviewService(sessions).submit(review)


@router.post("/reviews/{review_id}/corrections", status_code=status.HTTP_201_CREATED)
def correct_review(review_id: str, body: ReviewCorrection, sessions: Sessions) -> HumanReview:
    changes = body.model_dump(exclude={"reason"})
    return ReviewService(sessions).correct(review_id, reason=body.reason, **changes)
