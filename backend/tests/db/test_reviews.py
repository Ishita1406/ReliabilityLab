"""Evaluation results, failure classifications, and human reviews in PostgreSQL."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.reviews import (
    load_evaluation,
    load_failure_classification,
    load_review,
    save_evaluation,
    save_failure_classification,
    save_review,
)
from app.db.runs import load_trace
from app.domain.enums import (
    ClassificationSource,
    CriterionType,
    FailureCategory,
    ReviewTargetType,
    ReviewVerdict,
    Severity,
)
from app.domain.evaluation import CriterionResult, EvaluationResult, FailureClassification
from app.domain.ids import IdPrefix, new_id
from app.domain.review import HumanReviewAnnotation
from app.runner.runner import ScenarioRunner
from app.runner.samples import SAMPLE_AGENT_VERSION, deterministic_research_agent
from tests.db.helpers import EVALUATOR, SAMPLE_SUITE, make_service, scenario_ids, versioning

WHEN = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def run_id(sessions: sessionmaker[Session]) -> str:
    service = make_service(sessions)
    experiment = service.create_experiment(
        name="reviews",
        suite_id=SAMPLE_SUITE.id,
        suite_version=SAMPLE_SUITE.version,
        scenario_ids=scenario_ids("fetch-misleading"),
        agent_version_id=SAMPLE_AGENT_VERSION.id,
        repetitions=1,
        seed=0,
        **versioning(),
    )
    (stored_run,) = service.run_experiment(
        experiment.id, ScenarioRunner(deterministic_research_agent), EVALUATOR
    )
    return stored_run


def _evaluation(run_id: str) -> EvaluationResult:
    criteria = [
        CriterionResult(
            criterion_id="declares-answered",
            criterion_type=CriterionType.DECLARED_OUTCOME,
            passed=False,
            weight=2.0,
            required=True,
            detail="declared insufficient",
        ),
        CriterionResult(
            criterion_id="cites",
            criterion_type=CriterionType.CITATIONS_WITHIN,
            passed=True,
            weight=1.0,
            required=False,
        ),
    ]
    return EvaluationResult(
        run_id=run_id,
        evaluator_version="0.0.1",
        criterion_results=criteria,
        pass_threshold=0.5,
        score=1 / 3,
        passed=False,
        created_at=WHEN,
    )


def test_evaluation_classification_and_reviews_round_trip(session: Session, run_id: str) -> None:
    evaluation = _evaluation(run_id)
    save_evaluation(session, evaluation)
    evidence = [e.id for e in load_trace(session, run_id).events[-2:]]
    classification = FailureClassification(
        run_id=run_id,
        evaluation_result_id=evaluation.id,
        category=FailureCategory.INCORRECT_ANSWER,
        severity=Severity.MEDIUM,
        source=ClassificationSource.RULE,
        rationale="Did not answer.",
        evidence_event_ids=evidence,
        created_at=WHEN,
    )
    save_failure_classification(session, classification)
    reviews = [
        HumanReviewAnnotation(
            target_type=ReviewTargetType.RUN,
            target_id=run_id,
            reviewer="r@example.com",
            verdict=ReviewVerdict.AGREE,
            created_at=WHEN,
        ),
        HumanReviewAnnotation(
            target_type=ReviewTargetType.EVALUATION_RESULT,
            target_id=evaluation.id,
            reviewer="r@example.com",
            verdict=ReviewVerdict.DISAGREE,
            corrected_passed=True,
            comment="The conflict report is the right behaviour.",
            created_at=WHEN,
        ),
        HumanReviewAnnotation(
            target_type=ReviewTargetType.FAILURE_CLASSIFICATION,
            target_id=classification.id,
            reviewer="r@example.com",
            verdict=ReviewVerdict.DISAGREE,
            corrected_failure_category=FailureCategory.UNWARRANTED_ABSTENTION,
            created_at=WHEN,
        ),
        HumanReviewAnnotation(
            target_type=ReviewTargetType.TRACE_EVENT,
            target_id=evidence[0],
            reviewer="r@example.com",
            verdict=ReviewVerdict.NEEDS_DISCUSSION,
            comment="Look here.",
            created_at=WHEN,
        ),
    ]
    for review in reviews:
        save_review(session, review)
    session.expunge_all()

    assert load_evaluation(session, evaluation.id) == evaluation
    assert load_failure_classification(session, classification.id) == classification
    assert [load_review(session, r.id) for r in reviews] == reviews


def test_review_of_a_missing_target_is_refused(session: Session, run_id: str) -> None:
    review = HumanReviewAnnotation(
        target_type=ReviewTargetType.EVALUATION_RESULT,
        target_id=new_id(IdPrefix.EVALUATION_RESULT),
        reviewer="r",
        verdict=ReviewVerdict.AGREE,
        created_at=WHEN,
    )

    with (
        pytest.raises(IntegrityError, match="fk_review_annotations_evaluation_result_id"),
        session.begin_nested(),
    ):
        save_review(session, review)


def test_classification_evaluation_must_be_for_the_same_run(
    session: Session, run_id: str, sessions: sessionmaker[Session]
) -> None:
    evaluation = _evaluation(run_id)
    save_evaluation(session, evaluation)
    other_run = new_id(IdPrefix.RUN)
    classification = FailureClassification(
        run_id=other_run,
        evaluation_result_id=evaluation.id,
        category=FailureCategory.TIMEOUT,
        severity=Severity.LOW,
        source=ClassificationSource.RULE,
        rationale="x",
        created_at=WHEN,
    )

    with pytest.raises(IntegrityError), session.begin_nested():
        save_failure_classification(session, classification)
