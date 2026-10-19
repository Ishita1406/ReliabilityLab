"""Review records and append-only correction history."""

from datetime import timedelta

import pytest

from app.evaluation.models import FailureLabel
from app.review.fixtures import EPOCH, EVALUATOR_VERSION
from app.review.models import (
    CategoryVerdict,
    HumanReview,
    ReviewHistoryError,
    ReviewLabel,
    current_reviews,
    history,
)

RUN = "run_" + "1" * 32
OTHER_RUN = "run_" + "2" * 32


def review(
    reviewer: str = "alice",
    label: ReviewLabel = ReviewLabel.PASS,
    *,
    run_id: str = RUN,
    minutes: int = 0,
    supersedes: HumanReview | None = None,
    **fields: object,
) -> HumanReview:
    return HumanReview.model_validate(
        {
            "run_id": run_id,
            "case_id": "c",
            "scenario_id": "c.s",
            "evaluator_version": EVALUATOR_VERSION,
            "reviewer": reviewer,
            "label": label,
            "blind": True,
            "supersedes": supersedes.id if supersedes else None,
            "correction_reason": "changed my mind" if supersedes else None,
            "created_at": EPOCH + timedelta(minutes=minutes),
            **fields,
        }
    )


def test_non_definite_labels_need_a_rationale() -> None:
    for label in (ReviewLabel.AMBIGUOUS, ReviewLabel.INSUFFICIENTLY_SPECIFIED):
        with pytest.raises(ValueError, match="need a rationale"):
            review(label=label)
        assert not review(label=label, rationale="the case does not say").label.definite
    assert ReviewLabel.PASS.definite and ReviewLabel.FAIL.definite


def test_evaluator_uncertainty_is_not_a_reviewer_category() -> None:
    with pytest.raises(ValueError, match="describes the evaluator"):
        review(categories={FailureLabel.EVALUATOR_UNCERTAINTY: CategoryVerdict.PRESENT})


def test_corrections_need_both_link_and_reason() -> None:
    first = review()
    with pytest.raises(ValueError, match="both supersedes and correction_reason"):
        review(supersedes=None, correction_reason="why")
    with pytest.raises(ValueError, match="both supersedes and correction_reason"):
        HumanReview.model_validate({**review().model_dump(), "supersedes": first.id})
    with pytest.raises(ValueError, match="cannot supersede itself"):
        HumanReview.model_validate(
            {**first.model_dump(), "supersedes": first.id, "correction_reason": "x"}
        )


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValueError):
        review(confidence=1.5)
    assert review(confidence=0.3).confidence == 0.3


def test_correction_replaces_the_current_label_and_keeps_history() -> None:
    first = review(label=ReviewLabel.PASS)
    second = review(label=ReviewLabel.FAIL, minutes=5, supersedes=first)
    third = review(label=ReviewLabel.AMBIGUOUS, minutes=9, supersedes=second, rationale="unclear")
    bob = review("bob", ReviewLabel.FAIL)
    records = [first, second, third, bob]

    assert {r.id for r in current_reviews(records)} == {third.id, bob.id}
    assert history(records, third) == [first, second, third]
    assert first.label is ReviewLabel.PASS  # the original judgment is untouched


def test_second_independent_review_by_the_same_reviewer_is_rejected() -> None:
    with pytest.raises(ReviewHistoryError, match="must supersede the first"):
        current_reviews([review(), review(minutes=1)])
    # Same reviewer, different run: two independent chains are fine.
    assert len(current_reviews([review(), review(run_id=OTHER_RUN)])) == 2


def test_forks_cross_reviewer_corrections_and_time_travel_are_rejected() -> None:
    first = review()
    with pytest.raises(ReviewHistoryError, match="superseded twice"):
        current_reviews(
            [first, review(minutes=1, supersedes=first), review(minutes=2, supersedes=first)]
        )
    with pytest.raises(ReviewHistoryError, match="same reviewer"):
        current_reviews([first, review("bob", minutes=1, supersedes=first)])
    with pytest.raises(ReviewHistoryError, match="older than"):
        current_reviews([first, review(minutes=-1, supersedes=first)])
    with pytest.raises(ReviewHistoryError, match="unknown review"):
        current_reviews([review(minutes=1, supersedes=first)])


def test_cycles_are_rejected() -> None:
    a = review()
    b = review(minutes=0, supersedes=a)  # equal timestamps: only the cycle check can catch it
    looped = a.model_copy(update={"supersedes": b.id, "correction_reason": "loop"})
    with pytest.raises(ReviewHistoryError, match="cycle"):
        current_reviews([looped, b])
