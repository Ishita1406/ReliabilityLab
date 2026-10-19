"""Persistence for append-only human reviews (Milestone 8).

The database enforces the history rules: one root review per (run, evaluator version,
reviewer); a correction must supersede a review of the same chain; a review is superseded at
most once; rows cannot be updated or deleted (trigger). This module adds the checks that need
other tables: the review's case and scenario must be the run's, and a correction may not be
older than what it corrects.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.benchmarks import NotFoundError
from app.db.models import BenchmarkCaseRow, BenchmarkScenarioRow, HumanReviewRow, RunRow
from app.evaluation.models import FailureLabel
from app.review.models import CategoryVerdict, HumanReview, ReviewEvidence


class ReviewConsistencyError(ValueError):
    """The review does not match its run, or its correction history is invalid."""


def _to_row(review: HumanReview) -> HumanReviewRow:
    return HumanReviewRow(
        id=review.id,
        run_id=review.run_id,
        evaluator_version=review.evaluator_version,
        case_id=review.case_id,
        scenario_id=review.scenario_id,
        reviewer=review.reviewer,
        label=review.label,
        categories={str(k): v.value for k, v in review.categories.items()},
        confidence=review.confidence,
        evidence=[e.model_dump(mode="json") for e in review.evidence],
        rationale=review.rationale,
        blind=review.blind,
        provenance=review.provenance,
        supersedes=review.supersedes,
        correction_reason=review.correction_reason,
        created_at=review.created_at,
    )


def _from_row(row: HumanReviewRow) -> HumanReview:
    return HumanReview(
        id=row.id,
        run_id=row.run_id,
        evaluator_version=row.evaluator_version,
        case_id=row.case_id,
        scenario_id=row.scenario_id,
        reviewer=row.reviewer,
        label=row.label,
        categories={FailureLabel(k): CategoryVerdict(v) for k, v in row.categories.items()},
        confidence=row.confidence,
        evidence=[ReviewEvidence.model_validate(e) for e in row.evidence],
        rationale=row.rationale,
        blind=row.blind,
        provenance="synthetic" if row.provenance == "synthetic" else "human",
        supersedes=row.supersedes,
        correction_reason=row.correction_reason,
        created_at=row.created_at,
    )


def save_human_review(session: Session, review: HumanReview) -> None:
    located = session.execute(
        select(BenchmarkCaseRow.slug, BenchmarkScenarioRow.slug)
        .select_from(RunRow)
        .join(BenchmarkScenarioRow, BenchmarkScenarioRow.pk == RunRow.scenario_pk)
        .join(BenchmarkCaseRow, BenchmarkCaseRow.pk == BenchmarkScenarioRow.case_pk)
        .where(RunRow.id == review.run_id)
    ).one_or_none()
    if located is None:
        raise NotFoundError(f"run '{review.run_id}' not found")
    if tuple(located) != (review.case_id, review.scenario_id):
        raise ReviewConsistencyError(
            f"run '{review.run_id}' is case '{located[0]}' / scenario '{located[1]}'"
        )
    if review.supersedes is not None:
        previous = session.get(HumanReviewRow, review.supersedes)
        if previous is None:
            raise NotFoundError(f"review '{review.supersedes}' not found")
        if review.created_at < previous.created_at:
            raise ReviewConsistencyError("a correction cannot be older than the review it corrects")
    session.add(_to_row(review))
    session.flush()


def load_human_review(session: Session, review_id: str) -> HumanReview:
    row = session.get(HumanReviewRow, review_id)
    if row is None:
        raise NotFoundError(f"review '{review_id}' not found")
    return _from_row(row)


def list_human_reviews(
    session: Session, *, experiment_id: str | None = None, run_id: str | None = None
) -> list[HumanReview]:
    """All records (including superseded ones), oldest first."""
    query = select(HumanReviewRow).order_by(HumanReviewRow.created_at, HumanReviewRow.id)
    if run_id is not None:
        query = query.where(HumanReviewRow.run_id == run_id)
    if experiment_id is not None:
        query = query.join(RunRow, RunRow.id == HumanReviewRow.run_id).where(
            RunRow.experiment_id == experiment_id
        )
    return [_from_row(row) for row in session.scalars(query)]
