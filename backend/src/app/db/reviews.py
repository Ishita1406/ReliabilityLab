"""Persistence for evaluation results, failure classifications, and human review annotations."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.benchmarks import NotFoundError
from app.db.models import (
    CriterionResultRow,
    EvaluationResultRow,
    FailureClassificationRow,
    FailureEvidenceRow,
    ReviewAnnotationRow,
)
from app.domain.enums import ReviewTargetType
from app.domain.evaluation import CriterionResult, EvaluationResult, FailureClassification
from app.domain.review import HumanReviewAnnotation

_TARGET_COLUMN = {
    ReviewTargetType.RUN: "run_id",
    ReviewTargetType.EVALUATION_RESULT: "evaluation_result_id",
    ReviewTargetType.FAILURE_CLASSIFICATION: "failure_classification_id",
    ReviewTargetType.TRACE_EVENT: "trace_event_id",
}


def save_evaluation(session: Session, result: EvaluationResult) -> EvaluationResultRow:
    row = EvaluationResultRow(
        id=result.id,
        run_id=result.run_id,
        evaluator_version=result.evaluator_version,
        pass_threshold=result.pass_threshold,
        score=result.score,
        passed=result.passed,
        created_at=result.created_at,
    )
    session.add(row)
    session.flush()
    session.add_all(
        CriterionResultRow(
            evaluation_result_id=result.id,
            criterion_slug=item.criterion_id,
            position=position,
            criterion_type=item.criterion_type,
            passed=item.passed,
            weight=item.weight,
            required=item.required,
            detail=item.detail,
        )
        for position, item in enumerate(result.criterion_results)
    )
    session.flush()
    return row


def load_evaluation(session: Session, evaluation_id: str) -> EvaluationResult:
    row = session.get(EvaluationResultRow, evaluation_id)
    if row is None:
        raise NotFoundError(f"evaluation result '{evaluation_id}' not found")
    criteria = session.scalars(
        select(CriterionResultRow)
        .where(CriterionResultRow.evaluation_result_id == evaluation_id)
        .order_by(CriterionResultRow.position)
    ).all()
    return EvaluationResult(
        id=row.id,
        run_id=row.run_id,
        evaluator_version=row.evaluator_version,
        criterion_results=[
            CriterionResult(
                criterion_id=c.criterion_slug,
                criterion_type=c.criterion_type,
                passed=c.passed,
                weight=c.weight,
                required=c.required,
                detail=c.detail,
            )
            for c in criteria
        ],
        pass_threshold=row.pass_threshold,
        score=row.score,
        passed=row.passed,
        created_at=row.created_at,
    )


def save_failure_classification(
    session: Session, classification: FailureClassification
) -> FailureClassificationRow:
    row = FailureClassificationRow(
        id=classification.id,
        run_id=classification.run_id,
        evaluation_result_id=classification.evaluation_result_id,
        category=classification.category,
        severity=classification.severity,
        source=classification.source,
        rationale=classification.rationale,
        created_at=classification.created_at,
    )
    session.add(row)
    session.flush()
    session.add_all(
        FailureEvidenceRow(
            classification_id=classification.id, trace_event_id=event_id, position=position
        )
        for position, event_id in enumerate(classification.evidence_event_ids)
    )
    session.flush()
    return row


def load_failure_classification(session: Session, classification_id: str) -> FailureClassification:
    row = session.get(FailureClassificationRow, classification_id)
    if row is None:
        raise NotFoundError(f"failure classification '{classification_id}' not found")
    evidence = session.scalars(
        select(FailureEvidenceRow.trace_event_id)
        .where(FailureEvidenceRow.classification_id == classification_id)
        .order_by(FailureEvidenceRow.position)
    ).all()
    return FailureClassification(
        id=row.id,
        run_id=row.run_id,
        evaluation_result_id=row.evaluation_result_id,
        category=row.category,
        severity=row.severity,
        source=row.source,
        rationale=row.rationale,
        evidence_event_ids=list(evidence),
        created_at=row.created_at,
    )


def save_review(session: Session, review: HumanReviewAnnotation) -> ReviewAnnotationRow:
    targets: dict[str, str | None] = dict.fromkeys(_TARGET_COLUMN.values())
    targets[_TARGET_COLUMN[review.target_type]] = review.target_id
    row = ReviewAnnotationRow(
        id=review.id,
        target_type=review.target_type,
        reviewer=review.reviewer,
        verdict=review.verdict,
        comment=review.comment,
        corrected_failure_category=review.corrected_failure_category,
        corrected_passed=review.corrected_passed,
        created_at=review.created_at,
        updated_at=review.updated_at,
        **targets,
    )
    session.add(row)
    session.flush()
    return row


def load_review(session: Session, review_id: str) -> HumanReviewAnnotation:
    row = session.get(ReviewAnnotationRow, review_id)
    if row is None:
        raise NotFoundError(f"review annotation '{review_id}' not found")
    target_id = getattr(row, _TARGET_COLUMN[row.target_type])
    return HumanReviewAnnotation(
        id=row.id,
        target_type=row.target_type,
        target_id=target_id,
        reviewer=row.reviewer,
        verdict=row.verdict,
        comment=row.comment,
        corrected_failure_category=row.corrected_failure_category,
        corrected_passed=row.corrected_passed,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
