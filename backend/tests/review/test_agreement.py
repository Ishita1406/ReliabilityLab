"""Agreement metrics against hand-calculated values.

Every expected number is derived by hand in the comments, not computed with the code under
test. Fixture layout: ``app/review/fixtures.py``.
"""

import pytest

from app.evaluation.models import FailureLabel, Verdict
from app.review.agreement import (
    CalibrationReport,
    ConfusionMatrix,
    ReferenceStatus,
    binary_agreement,
    calibration_report,
    kappa_from_counts,
    reference_label,
)
from app.review.fixtures import (
    EVALUATOR_VERSION,
    FixtureRun,
    build,
    calibration_fixture,
    calibration_runs,
    imbalanced_fixture,
)
from app.review.models import ReviewLabel
from tests.review.test_models import review

P, F = ReviewLabel.PASS, ReviewLabel.FAIL


def report(*, require_blind: bool = True) -> CalibrationReport:
    evaluations, reviews = calibration_fixture()
    return calibration_report(
        evaluations, reviews, evaluator_version=EVALUATOR_VERSION, require_blind=require_blind
    )


# --- the 2x2 textbook example -------------------------------------------------------------------


def test_kappa_textbook_example() -> None:
    # 50 items: both yes 20, A yes/B no 5, A no/B yes 10, both no 15.
    # p_o = 35/50 = 0.7; P(A yes) = 25/50 = 0.5; P(B yes) = 30/50 = 0.6
    # p_e = 0.5*0.6 + 0.5*0.4 = 0.5; kappa = (0.7 - 0.5) / (1 - 0.5) = 0.4
    # SE = sqrt(0.7*0.3 / (50 * 0.5^2)) = sqrt(0.0168) = 0.129615; 1.96 * SE = 0.254043
    kappa = kappa_from_counts(20, 5, 10, 15, min_ci=10)
    assert kappa.value == pytest.approx(0.4)
    assert (kappa.ci_low, kappa.ci_high) == pytest.approx(
        (0.4 - 0.254043, 0.4 + 0.254043), abs=1e-5
    )


def test_binary_metrics_of_the_main_fixture() -> None:
    binary = report().automated_vs_human
    # TP = tp block 19 + corrected 1; FP = 5; FN = 10; TN = tn block 14 + single-reviewer 1.
    assert binary.matrix == ConfusionMatrix(tp=20, fp=5, fn=10, tn=15)
    assert binary.agreement.value == pytest.approx(35 / 50)
    assert binary.precision.value == pytest.approx(20 / 25)
    assert binary.recall.value == pytest.approx(20 / 30)
    # F1 = 2 * 0.8 * (2/3) / (0.8 + 2/3) = (16/15) / (22/15) = 8/11
    assert binary.f1.value == pytest.approx(8 / 11)
    assert binary.kappa.value == pytest.approx(0.4)
    assert binary.pabak.value == pytest.approx(2 * 0.7 - 1)
    assert binary.reference_positive_rate.value == pytest.approx(30 / 50)
    assert binary.predicted_positive_rate.value == pytest.approx(25 / 50)


def test_sample_summary_counts_every_group_separately() -> None:
    summary = report().summary
    # 19+5+10+14 core + corrected + single + 4 disputed + 3 ambiguous + 2 insufficient
    # + 3 abstained + 2 not blind = 64 runs
    assert summary.reviewed_runs == 64
    # alice 63 + 1 correction, bob 63, carol 1
    assert (summary.reviews, summary.corrections) == (128, 1)
    assert summary.reviewers == ["alice", "bob", "carol"]
    assert (summary.definite, summary.disputed, summary.ambiguous) == (55, 4, 3)
    assert summary.insufficiently_specified == 2
    assert (summary.automated_inconclusive, summary.excluded_not_blind) == (3, 2)
    assert (summary.compared, summary.synthetic_reviews) == (50, 128)


def test_reviewer_pair_agreement_and_kappa() -> None:
    [pair] = report().reviewer_pairs  # carol shares no run with anyone
    assert (pair.reviewer_a, pair.reviewer_b) == ("alice", "bob")
    # Both reviewed 63 runs; 5 have a non-definite label (3 ambiguous + 2 insufficient).
    assert (pair.both_reviewed, pair.both_definite, pair.one_or_both_not_definite) == (63, 58, 5)
    # both fail: 19 tp + 10 fn + 3 abstained + 1 corrected = 33; alice fail/bob pass: 4 disputed;
    # bob fail/alice pass: 0; both pass: 5 fp + 14 tn + 2 not blind = 21.
    # p_o = 54/58; P(alice fail) = 37/58; P(bob fail) = 33/58
    # p_e = (37*33 + 21*25) / 58^2 = 1746/3364
    # kappa = (54*58 - 1746) / (3364 - 1746) = 1386/1618
    assert pair.agreement.value == pytest.approx(54 / 58)
    assert pair.kappa.value == pytest.approx(1386 / 1618)
    assert len(pair.disagreements) == 4


def test_per_category_agreement() -> None:
    categories = {c.category: c for c in report().categories}
    assert set(categories) == {FailureLabel.SECURITY_VIOLATION, FailureLabel.CONTEXT_RETENTION}

    security = categories[FailureLabel.SECURITY_VIOLATION]
    # tp-0 TP, tp-1 FN, tn-0 FP, tn-1..3 TN; tn-4 (alice present, bob unsure) has no reference.
    assert security.human_assessed_runs == 6
    assert security.agreement.matrix == ConfusionMatrix(tp=1, fp=1, fn=1, tn=3)
    # p_o = 4/6; P(pred) = P(ref) = 2/6; p_e = (1/3)^2 + (2/3)^2 = 5/9
    # kappa = (2/3 - 5/9) / (4/9) = 1/4
    assert security.agreement.kappa.value == pytest.approx(0.25)
    assert len(security.disagreements) == 2

    context = categories[FailureLabel.CONTEXT_RETENTION]
    # fn-0: only alice assessed (present); the evaluator did not flag it -> one FN.
    assert context.agreement.matrix == ConfusionMatrix(fn=1)
    assert context.agreement.precision.value is None  # 0/0, not 0
    assert context.agreement.recall.value == 0.0
    assert context.agreement.f1.value is None


def test_intervals_only_with_enough_samples() -> None:
    small = binary_agreement(ConfusionMatrix(tp=3, fp=1, fn=1, tn=4), min_ci=10)
    assert small.agreement.ci_low is None and small.agreement.note == "no interval: n < 10"
    assert small.kappa.ci_low is None
    large = binary_agreement(ConfusionMatrix(tp=3, fp=1, fn=1, tn=5), min_ci=10)
    assert large.agreement.ci_low is not None and large.kappa.ci_low is not None


def test_kappa_paradox_under_class_imbalance() -> None:
    evaluations, reviews = imbalanced_fixture()
    result = calibration_report(evaluations, reviews, evaluator_version=EVALUATOR_VERSION)
    binary = result.automated_vs_human
    # TN 90, FP 5, FN 5, TP 0: p_o = 0.9; P(pred fail) = P(ref fail) = 0.05
    # p_e = 0.05^2 + 0.95^2 = 0.905; kappa = (0.9 - 0.905) / 0.095 = -1/19
    assert binary.matrix == ConfusionMatrix(fp=5, fn=5, tn=90)
    assert binary.agreement.value == pytest.approx(0.9)
    assert binary.kappa.value == pytest.approx(-1 / 19)
    assert binary.pabak.value == pytest.approx(0.8)
    assert (binary.precision.value, binary.recall.value, binary.f1.value) == (0.0, 0.0, 0.0)
    assert any("Class imbalance" in c for c in result.caveats)


def test_degenerate_cases_are_undefined_not_zero() -> None:
    all_pass = binary_agreement(ConfusionMatrix(tn=12), min_ci=10)
    assert all_pass.precision.unavailable_reason == "the evaluator predicted no failures"
    assert all_pass.recall.unavailable_reason == "the reference has no failures"
    assert all_pass.f1.value is None
    assert all_pass.kappa.value is None
    assert "kappa is undefined" in (all_pass.kappa.unavailable_reason or "")
    assert all_pass.agreement.value == 1.0
    empty = binary_agreement(ConfusionMatrix(), min_ci=10)
    assert empty.agreement.value is None and empty.kappa.value is None


def test_perfect_agreement_on_both_classes_is_kappa_one() -> None:
    assert kappa_from_counts(4, 0, 0, 6, min_ci=10).value == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ([P], (ReferenceStatus.DEFINITE, P)),
        ([F, F], (ReferenceStatus.DEFINITE, F)),
        ([P, F], (ReferenceStatus.DISPUTED, None)),
        ([P, F, ReviewLabel.AMBIGUOUS], (ReferenceStatus.DISPUTED, None)),
        ([P, ReviewLabel.AMBIGUOUS], (ReferenceStatus.AMBIGUOUS, None)),
        (
            [ReviewLabel.AMBIGUOUS, ReviewLabel.INSUFFICIENTLY_SPECIFIED],
            (ReferenceStatus.INSUFFICIENTLY_SPECIFIED, None),
        ),
    ],
)
def test_reference_label_rules(
    labels: list[ReviewLabel], expected: tuple[ReferenceStatus, ReviewLabel | None]
) -> None:
    reviews = [
        review(f"r{i}", label, rationale=None if label.definite else "unclear")
        for i, label in enumerate(labels)
    ]
    assert reference_label(reviews) == expected


def test_the_evaluator_is_never_its_own_reference() -> None:
    evaluations, _ = calibration_fixture()
    result = calibration_report(evaluations, [], evaluator_version=EVALUATOR_VERSION)
    assert result.automated_vs_human.matrix.n == 0
    assert result.automated_vs_human.agreement.value is None
    assert not result.empirical  # no reviews at all is not an empirical calibration either


def test_correction_changes_the_current_label_only() -> None:
    evaluations, reviews = calibration_fixture()
    assert sum(1 for r in reviews if r.supersedes is not None) == 1
    # Dropping the correction leaves alice's original 'pass' vs bob's 'fail': a dispute.
    without = [r for r in reviews if r.supersedes is None]
    result = calibration_report(evaluations, without, evaluator_version=EVALUATOR_VERSION)
    assert result.summary.disputed == 5
    assert result.automated_vs_human.matrix.tp == 19


def test_non_blind_reviews_can_be_included_explicitly() -> None:
    included = report(require_blind=False)
    # The two not-blind runs are evaluator 'fail', human 'pass': two more FPs.
    assert included.automated_vs_human.matrix == ConfusionMatrix(tp=20, fp=7, fn=10, tn=15)
    assert included.summary.excluded_not_blind == 0


def test_scope_and_evaluator_version_filters() -> None:
    evaluations, reviews = calibration_fixture()
    tp_ids = {r.run_id for r in calibration_runs() if r.name.startswith("tp-")}
    scoped = calibration_report(
        evaluations, reviews, evaluator_version=EVALUATOR_VERSION, run_ids=tp_ids, scope="sampled"
    )
    assert scoped.automated_vs_human.matrix == ConfusionMatrix(tp=19)
    assert not any("random calibration sample" in c for c in scoped.caveats)
    other = calibration_report(evaluations, reviews, evaluator_version="evaluator-9.9.9")
    assert other.summary.reviews == 0


def test_mixing_evaluator_versions_is_an_error() -> None:
    run = FixtureRun("x", Verdict.PASSED, {"alice": P})
    evaluations, reviews = build([run])
    stale = {
        k: v.model_copy(update={"evaluator_version": "evaluator-0.0.1"})
        for k, v in evaluations.items()
    }
    with pytest.raises(ValueError, match="another evaluator version"):
        calibration_report(stale, reviews, evaluator_version=EVALUATOR_VERSION)


def test_caveats_flag_synthetic_and_excluded_data() -> None:
    caveats = " ".join(report().caveats)
    assert "Not an empirical calibration: 128 of 128 reviews are synthetic" in caveats
    assert "9 reviewed run(s) have no definite human reference" in caveats
    assert "2 run(s) excluded because a reviewer saw the automated verdict" in caveats
    assert "abstained (inconclusive) on 3 run(s)" in caveats
