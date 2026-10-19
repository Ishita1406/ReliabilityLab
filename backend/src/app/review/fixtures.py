"""SYNTHETIC annotation fixtures with known disagreements and edge cases.

These exist to validate the metric *implementation* against hand-calculated values. Every
review has ``provenance="synthetic"``, so any report built from them says it is not an
empirical calibration. Nothing here says anything about how well the real evaluator works.

``calibration_fixture()`` (reviewers alice and bob, plus carol on one run):

=======================  =====  =========  =====================================  =========
block                    runs   automated  human reviews                          note
=======================  =====  =========  =====================================  =========
tp                       19     fail       alice fail, bob fail
fp                       5      fail       alice pass, bob pass
fn                       10     pass       alice fail, bob fail
tn                       14     pass       alice pass, bob pass
corrected                1      fail       alice pass -> corrected to fail; bob   TP after
                                           fail                                   correction
single-reviewer          1      pass       carol pass                             TN
disputed                 4      fail       alice fail, bob pass                   excluded
ambiguous                3      pass       alice ambiguous, bob fail              excluded
insufficient             2      fail       alice insufficiently_specified, bob    excluded
                                           ambiguous
abstained                3      incon.     alice fail, bob fail                   excluded
not-blind                2      fail       alice pass, bob pass (saw verdict)     excluded
=======================  =====  =========  =====================================  =========

Categories (on blind, compared runs): ``security_violation`` on tp-0 (auto+human present),
tp-1 (human present only), tn-0 (auto only), tn-1..3 (neither), tn-4 (alice present, bob
unsure: no reference); ``context_retention`` on fn-0 (alice present, bob did not assess).

``imbalanced_fixture()``: 100 runs, one reviewer, TN 90 / FP 5 / FN 5 / TP 0 - the kappa paradox.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.enums import CriterionType, RunStatus
from app.domain.ids import IdPrefix, derived_id
from app.evaluation.models import (
    CaseEvaluation,
    CriterionEvaluation,
    CriterionStatus,
    EvaluationLayer,
    EvaluatorKind,
    FailureLabel,
    LabelAssignment,
    LayerSummary,
    Verdict,
)
from app.evaluation.scoring import decide
from app.review.models import CategoryVerdict, HumanReview, ReviewLabel

EVALUATOR_VERSION = "evaluator-0.1.0"
CASE_ID = "synthetic.calibration"
EPOCH = datetime(2026, 5, 1, tzinfo=UTC)

Categories = dict[FailureLabel, CategoryVerdict]


@dataclass
class FixtureRun:
    name: str
    automated: Verdict
    reviews: dict[str, ReviewLabel]
    automated_labels: list[FailureLabel] = field(default_factory=list)
    categories: dict[str, Categories] = field(default_factory=dict)
    blind: bool = True
    corrected_from: dict[str, ReviewLabel] = field(default_factory=dict)
    """reviewer -> original label that was later corrected to ``reviews[reviewer]``."""

    @property
    def run_id(self) -> str:
        return derived_id(IdPrefix.RUN, "synthetic-review", self.name)


def _criterion(
    cid: str, status: CriterionStatus, labels: list[FailureLabel], *, required: bool, weight: float
) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion_id=cid,
        criterion_type=CriterionType.DECLARED_OUTCOME,
        layer=EvaluationLayer.ANSWER,
        evaluator=EvaluatorKind.DETERMINISTIC,
        status=status,
        weight=weight,
        required=required,
        explanation="synthetic fixture",
        labels=labels,
    )


def fixture_evaluation(run: FixtureRun) -> CaseEvaluation:
    """A valid evaluation with the given verdict and (extra) failure labels."""
    status = {
        Verdict.PASSED: CriterionStatus.PASSED,
        Verdict.FAILED: CriterionStatus.FAILED,
        Verdict.INCONCLUSIVE: CriterionStatus.INCONCLUSIVE,
    }[run.automated]
    main_labels = {
        CriterionStatus.PASSED: [],
        CriterionStatus.FAILED: [FailureLabel.TASK_CORRECTNESS],
        CriterionStatus.INCONCLUSIVE: [FailureLabel.EVALUATOR_UNCERTAINTY],
    }[status]
    criteria = [_criterion("main", status, main_labels, required=True, weight=1.0)]
    # Optional, low-weight failed criteria carry extra labels without changing the verdict.
    criteria += [
        _criterion(f"extra-{i}", CriterionStatus.FAILED, [label], required=False, weight=0.1)
        for i, label in enumerate(run.automated_labels)
    ]
    decision = decide(criteria, 0.5)
    if decision.verdict is not run.automated:
        raise ValueError(f"fixture '{run.name}' does not produce verdict {run.automated}")
    grouped: dict[FailureLabel, list[str]] = {}
    for criterion in criteria:
        for label in criterion.labels:
            grouped.setdefault(label, []).append(criterion.criterion_id)
    return CaseEvaluation(
        case_id=CASE_ID,
        scenario_id=f"{CASE_ID}.{run.name}",
        run_id=run.run_id,
        run_status=RunStatus.COMPLETED,
        evaluator_version=EVALUATOR_VERSION,
        verdict=decision.verdict,
        score=decision.score,
        score_upper_bound=decision.upper_bound,
        pass_threshold=0.5,
        criteria=criteria,
        layers=[
            LayerSummary(
                layer=EvaluationLayer.ANSWER,
                verdict=decision.verdict,
                score=None,
                criterion_ids=[c.criterion_id for c in criteria],
            )
        ],
        failure_labels=[
            LabelAssignment(label=label, criterion_ids=ids, rationale="synthetic")
            for label, ids in grouped.items()
        ],
        explanation="synthetic fixture",
        created_at=EPOCH,
    )


def _review(
    run: FixtureRun,
    reviewer: str,
    label: ReviewLabel,
    created_at: datetime,
    *,
    supersedes: str | None = None,
) -> HumanReview:
    return HumanReview(
        id=derived_id(IdPrefix.HUMAN_REVIEW, run.name, reviewer, str(created_at)),
        run_id=run.run_id,
        case_id=CASE_ID,
        scenario_id=f"{CASE_ID}.{run.name}",
        evaluator_version=EVALUATOR_VERSION,
        reviewer=reviewer,
        label=label,
        categories=run.categories.get(reviewer, {}),
        rationale=None if label.definite else "synthetic: outcome cannot be decided",
        blind=run.blind,
        provenance="synthetic",
        supersedes=supersedes,
        correction_reason="synthetic: re-read the trace" if supersedes else None,
        created_at=created_at,
    )


def build(runs: list[FixtureRun]) -> tuple[dict[str, CaseEvaluation], list[HumanReview]]:
    evaluations = {run.run_id: fixture_evaluation(run) for run in runs}
    reviews: list[HumanReview] = []
    for index, run in enumerate(runs):
        at = EPOCH + timedelta(minutes=index)
        for reviewer, label in run.reviews.items():
            original = run.corrected_from.get(reviewer)
            if original is None:
                reviews.append(_review(run, reviewer, label, at))
                continue
            first = _review(run, reviewer, original, at)
            reviews += [
                first,
                _review(run, reviewer, label, at + timedelta(days=1), supersedes=first.id),
            ]
    return evaluations, reviews


P, F = ReviewLabel.PASS, ReviewLabel.FAIL
AMB, INSUF = ReviewLabel.AMBIGUOUS, ReviewLabel.INSUFFICIENTLY_SPECIFIED
PRESENT, ABSENT, UNSURE = CategoryVerdict.PRESENT, CategoryVerdict.ABSENT, CategoryVerdict.UNSURE
SEC, CTX = FailureLabel.SECURITY_VIOLATION, FailureLabel.CONTEXT_RETENTION


def _block(
    prefix: str, count: int, automated: Verdict, reviews: dict[str, ReviewLabel]
) -> list[FixtureRun]:
    return [FixtureRun(f"{prefix}-{i}", automated, dict(reviews)) for i in range(count)]


def both(label: ReviewLabel) -> dict[str, ReviewLabel]:
    return {"alice": label, "bob": label}


def one(label: ReviewLabel) -> dict[str, ReviewLabel]:
    return {"alice": label}


def calibration_runs() -> list[FixtureRun]:
    tp = _block("tp", 19, Verdict.FAILED, both(F))
    fp = _block("fp", 5, Verdict.FAILED, both(P))
    fn = _block("fn", 10, Verdict.PASSED, both(F))
    tn = _block("tn", 14, Verdict.PASSED, both(P))

    tp[0].automated_labels = [SEC]
    tp[0].categories = {"alice": {SEC: PRESENT}, "bob": {SEC: PRESENT}}
    tp[1].categories = {"alice": {SEC: PRESENT}, "bob": {SEC: PRESENT}}
    tn[0].automated_labels = [SEC]
    for run in (tn[0], tn[1], tn[2], tn[3]):
        run.categories = {"alice": {SEC: ABSENT}, "bob": {SEC: ABSENT}}
    tn[4].categories = {"alice": {SEC: PRESENT}, "bob": {SEC: UNSURE}}
    fn[0].categories = {"alice": {CTX: PRESENT}}

    return [
        *tp,
        *fp,
        *fn,
        *tn,
        FixtureRun("corrected", Verdict.FAILED, both(F), corrected_from={"alice": P}),
        FixtureRun("single-reviewer", Verdict.PASSED, {"carol": P}),
        *_block("disputed", 4, Verdict.FAILED, {"alice": F, "bob": P}),
        *_block("ambiguous", 3, Verdict.PASSED, {"alice": AMB, "bob": F}),
        *_block("insufficient", 2, Verdict.FAILED, {"alice": INSUF, "bob": AMB}),
        *_block("abstained", 3, Verdict.INCONCLUSIVE, both(F)),
        *[FixtureRun(f"not-blind-{i}", Verdict.FAILED, both(P), blind=False) for i in range(2)],
    ]


def calibration_fixture() -> tuple[dict[str, CaseEvaluation], list[HumanReview]]:
    return build(calibration_runs())


def imbalanced_fixture() -> tuple[dict[str, CaseEvaluation], list[HumanReview]]:
    return build(
        [
            *_block("tn", 90, Verdict.PASSED, one(P)),
            *_block("fp", 5, Verdict.FAILED, one(P)),
            *_block("fn", 5, Verdict.PASSED, one(F)),
        ]
    )
