"""Append-only human reviews in PostgreSQL and the review workflow on a stored experiment."""

import pytest
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.human_reviews import (
    ReviewConsistencyError,
    list_human_reviews,
    load_human_review,
    save_human_review,
)
from app.db.models import HumanReviewRow
from app.db.runs import load_run
from app.domain.agent import AgentConfig
from app.evaluation.engine import Evaluator
from app.evaluation.models import FailureLabel
from app.experiments.samples import (
    BASELINE_VERSION,
    EVALUATION_SUITE,
    RESEARCH_AGENT,
    research_agent_factory,
)
from app.experiments.service import ExperimentService
from app.review.models import CategoryVerdict, HumanReview, ReviewEvidence, ReviewLabel
from app.review.queue import QueuePolicy, QueueReason, SamplingPolicy
from app.review.service import ReviewService, ReviewWorkflowError
from app.runner.runner import ScenarioRunner
from app.runner.samples import SAMPLE_LIMITS
from tests.db.helpers import ticking_clock

PREFIX = "runner.lighthouse-year."
SCENARIOS = [PREFIX + n for n in ("baseline", "fetch-misleading", "fetch-prompt-injection")]
EVALUATOR = Evaluator()


@pytest.fixture
def experiment(sessions: sessionmaker[Session]) -> tuple[str, list[str]]:
    service = ExperimentService(sessions, now=ticking_clock())
    config = AgentConfig(
        id=BASELINE_VERSION.agent_config_id,
        name="deterministic-research",
        created_at=BASELINE_VERSION.created_at,
    )
    service.register(EVALUATION_SUITE, config, BASELINE_VERSION)
    experiment_id = service.create_experiment(
        name="reviewed",
        suite_id=EVALUATION_SUITE.id,
        suite_version=EVALUATION_SUITE.version,
        scenario_ids=SCENARIOS,
        agent_version_id=BASELINE_VERSION.id,
        repetitions=1,
        seed=0,
        limits=SAMPLE_LIMITS,
        evaluator_version=EVALUATOR.version,
        agent_implementation=RESEARCH_AGENT,
    ).id
    run_ids = service.run_experiment(
        experiment_id, ScenarioRunner(research_agent_factory), EVALUATOR
    )
    return experiment_id, run_ids


@pytest.fixture
def reviews(sessions: sessionmaker[Session]) -> ReviewService:
    return ReviewService(sessions, now=ticking_clock())


def draft(
    session: Session, run_id: str, reviewer: str, label: ReviewLabel, **fields: object
) -> HumanReview:
    run = load_run(session, run_id).run
    return HumanReview.model_validate(
        {
            "run_id": run_id,
            "case_id": run.case_id,
            "scenario_id": run.scenario_id,
            "evaluator_version": EVALUATOR.version,
            "reviewer": reviewer,
            "label": label,
            "blind": True,
            **fields,
        }
    )


def test_review_round_trip_with_categories_and_evidence(
    experiment: tuple[str, list[str]], reviews: ReviewService, session: Session
) -> None:
    _, run_ids = experiment
    review = draft(
        session,
        run_ids[1],
        "alice",
        ReviewLabel.FAIL,
        categories={FailureLabel.TASK_CORRECTNESS: CategoryVerdict.PRESENT},
        confidence=0.8,
        evidence=[ReviewEvidence(note="answer hedges between 1873 and 1901")],
    )
    stored = reviews.submit(review)
    assert load_human_review(session, stored.id) == stored


def test_correction_keeps_the_original_and_builds_history(
    experiment: tuple[str, list[str]], reviews: ReviewService, session: Session
) -> None:
    _, run_ids = experiment
    original = reviews.submit(draft(session, run_ids[0], "alice", ReviewLabel.FAIL))
    corrected = reviews.correct(original.id, reason="misread the citation", label=ReviewLabel.PASS)
    again = reviews.correct(
        corrected.id,
        reason="actually unclear",
        label=ReviewLabel.AMBIGUOUS,
        rationale="prompt does not say whether a note is required",
    )

    assert load_human_review(session, original.id).label is ReviewLabel.FAIL  # untouched
    assert [r.id for r in reviews.history(original.id)] == [original.id, corrected.id, again.id]
    assert [r.label for r in reviews.history(again.id)] == [
        ReviewLabel.FAIL,
        ReviewLabel.PASS,
        ReviewLabel.AMBIGUOUS,
    ]
    with pytest.raises(ReviewWorkflowError, match="already corrected"):
        reviews.correct(original.id, reason="fork", label=ReviewLabel.PASS)
    with pytest.raises(ReviewWorkflowError, match="cannot change who or what"):
        reviews.correct(again.id, reason="x", reviewer="mallory")


def test_rows_cannot_be_updated_or_deleted(
    experiment: tuple[str, list[str]], reviews: ReviewService, session: Session
) -> None:
    _, run_ids = experiment
    review = reviews.submit(draft(session, run_ids[0], "alice", ReviewLabel.PASS))
    with pytest.raises(DBAPIError, match="append-only: UPDATE"), session.begin_nested():
        session.execute(
            update(HumanReviewRow).where(HumanReviewRow.id == review.id).values(label="fail")
        )
    with pytest.raises(DBAPIError, match="append-only: DELETE"), session.begin_nested():
        session.execute(delete(HumanReviewRow).where(HumanReviewRow.id == review.id))
    with pytest.raises(DBAPIError, match="append-only: TRUNCATE"), session.begin_nested():
        session.execute(text("TRUNCATE human_reviews"))
    assert load_human_review(session, review.id).label is ReviewLabel.PASS


def test_database_enforces_one_chain_per_reviewer_and_no_forks(
    experiment: tuple[str, list[str]], session: Session
) -> None:
    _, run_ids = experiment
    first = draft(session, run_ids[0], "alice", ReviewLabel.PASS)
    save_human_review(session, first)
    with (
        pytest.raises(IntegrityError, match="uq_human_reviews_one_chain_root"),
        session.begin_nested(),
    ):
        save_human_review(session, draft(session, run_ids[0], "alice", ReviewLabel.FAIL))

    correction = draft(
        session, run_ids[0], "alice", ReviewLabel.FAIL, supersedes=first.id, correction_reason="x"
    )
    save_human_review(session, correction)
    fork = draft(
        session,
        run_ids[0],
        "alice",
        ReviewLabel.AMBIGUOUS,
        rationale="r",
        supersedes=first.id,
        correction_reason="y",
    )
    with pytest.raises(IntegrityError, match="uq_human_reviews_supersedes"), session.begin_nested():
        save_human_review(session, fork)

    other_reviewer = draft(
        session,
        run_ids[0],
        "bob",
        ReviewLabel.FAIL,
        supersedes=correction.id,
        correction_reason="z",
    )
    with (
        pytest.raises(IntegrityError, match="fk_human_reviews_supersedes_same_chain"),
        session.begin_nested(),
    ):
        save_human_review(session, other_reviewer)


def test_review_must_match_its_run_and_an_existing_evaluation(
    experiment: tuple[str, list[str]], session: Session
) -> None:
    _, run_ids = experiment
    wrong_case = draft(session, run_ids[0], "alice", ReviewLabel.PASS).model_copy(
        update={"scenario_id": PREFIX + "fetch-latency"}
    )
    with pytest.raises(ReviewConsistencyError, match="is case"):
        save_human_review(session, wrong_case)
    unknown_evaluator = draft(session, run_ids[0], "alice", ReviewLabel.PASS).model_copy(
        update={"evaluator_version": "evaluator-9.9.9"}
    )
    with pytest.raises(IntegrityError, match="fk_human_reviews_evaluation"), session.begin_nested():
        save_human_review(session, unknown_evaluator)


def test_check_constraints_back_up_the_domain_rules(
    experiment: tuple[str, list[str]], session: Session
) -> None:
    _, run_ids = experiment
    row = {
        "id": "hrv_" + "a" * 32,
        "run_id": run_ids[0],
        "evaluator_version": EVALUATOR.version,
        "case_id": "runner.lighthouse-year",
        "scenario_id": SCENARIOS[0],
        "reviewer": "alice",
        "label": "ambiguous",
        "categories": {},
        "evidence": [],
        "blind": True,
        "provenance": "human",
        "created_at": text("now()"),
    }
    with (
        pytest.raises(IntegrityError, match="non_definite_needs_rationale"),
        session.begin_nested(),
    ):
        session.execute(insert(HumanReviewRow).values(**row))
    with pytest.raises(IntegrityError, match="provenance_values"), session.begin_nested():
        session.execute(
            insert(HumanReviewRow).values(**{**row, "label": "pass", "provenance": "llm"})
        )


def test_queue_and_report_on_a_stored_experiment(
    experiment: tuple[str, list[str]], reviews: ReviewService, session: Session
) -> None:
    experiment_id, run_ids = experiment
    queue = reviews.queue(experiment_id, QueuePolicy(sampling=SamplingPolicy(rate=1e-9)))
    assert [(i.scenario_id, i.reasons) for i in queue] == [
        (PREFIX + "fetch-prompt-injection", [QueueReason.SECURITY_SENSITIVE])
    ]

    for reviewer in ("alice", "bob"):
        reviews.submit(draft(session, run_ids[0], reviewer, ReviewLabel.PASS))  # evaluator: pass
        reviews.submit(draft(session, run_ids[1], reviewer, ReviewLabel.FAIL))  # evaluator: fail
    report = reviews.report(experiment_id, scope="all")
    assert report.summary.reviewed_runs == 2
    assert report.automated_vs_human.matrix.tp == 1 and report.automated_vs_human.matrix.tn == 1
    assert report.empirical  # provenance 'human' in this test, though the labels are invented
    sampled = reviews.report(experiment_id, policy=QueuePolicy(sampling=SamplingPolicy(rate=1e-9)))
    assert sampled.summary.reviewed_runs == 0
    assert len(list_human_reviews(session, experiment_id=experiment_id)) == 4
