"""Agreement between the automated evaluator and human reviewers, and between reviewers.

Reference labels come **only from humans**. The evaluator's prediction is never the reference.

* **Human reference per run** (from each reviewer's *current* review): all definite and equal
  -> that label; two definite labels conflict -> ``disputed``; otherwise, if anyone said
  ``insufficiently_specified`` -> that; otherwise ``ambiguous``. Only definite references enter
  the automated-vs-human metrics; the other groups are counted, not guessed.
* **Binary task**: the evaluator as a *failure detector*. Positive = ``fail``.
  TP = evaluator fail & human fail, FP = evaluator fail & human pass, FN = evaluator pass &
  human fail, TN = both pass. An ``inconclusive`` automated verdict is an abstention: counted,
  excluded from the matrix.
* **Independence**: Cohen's kappa assumes the two raters judged independently. A reviewer who
  saw the automated verdict is not independent of it, so by default (``require_blind``) runs
  with any non-blind current review are excluded from automated-vs-human metrics (counted).
* **Per category**: for each failure category, runs where the reviewers who assessed it agree
  on present/absent (nobody ``unsure``) form the reference; the evaluator's label set is the
  prediction.

Formulas (n = TP+FP+FN+TN): agreement p_o = (TP+TN)/n; precision = TP/(TP+FP);
recall = TP/(TP+FN); F1 = 2PR/(P+R); p_e = ((TP+FP)(TP+FN) + (FN+TN)(FP+TN))/n^2;
kappa = (p_o - p_e)/(1 - p_e); PABAK = 2 p_o - 1. Proportions get 95% Wilson intervals and
kappa an approximate large-sample interval (SE = sqrt(p_o(1-p_o)/(n(1-p_e)^2))), both only when
the denominator reaches ``min_sample_for_ci``. Undefined quantities (0/0, or kappa with
p_e = 1) are reported as unavailable with the reason, never as 0.
"""

import math
from collections.abc import Iterable, Mapping
from enum import StrEnum
from itertools import combinations
from typing import Literal

from pydantic import Field, NonNegativeInt

from app.domain.base import DomainModel, NonEmptyStr
from app.evaluation.models import CaseEvaluation, FailureLabel, Verdict
from app.experiments.stats import Z_95, wilson_interval
from app.review.models import CategoryVerdict, HumanReview, ReviewLabel, current_reviews

DEFAULT_MIN_SAMPLE_FOR_CI = 10
REVIEWABLE_CATEGORIES = [c for c in FailureLabel if c is not FailureLabel.EVALUATOR_UNCERTAINTY]


class Estimate(DomainModel):
    value: float | None
    n: NonNegativeInt = Field(description="Denominator (sample size) of the estimate.")
    ci_low: float | None = None
    ci_high: float | None = None
    unavailable_reason: NonEmptyStr | None = None
    note: str | None = None

    @classmethod
    def undefined(cls, reason: str, n: int) -> "Estimate":
        return cls(value=None, n=n, unavailable_reason=reason)


def proportion(k: int, n: int, *, min_ci: int, if_empty: str) -> Estimate:
    if n == 0:
        return Estimate.undefined(if_empty, 0)
    if n < min_ci:
        return Estimate(value=k / n, n=n, note=f"no interval: n < {min_ci}")
    low, high = wilson_interval(k, n)
    return Estimate(value=k / n, n=n, ci_low=low, ci_high=high, note="95% Wilson interval")


class ConfusionMatrix(DomainModel):
    """Rows: automated prediction; columns: human reference. Positive = fail."""

    tp: NonNegativeInt = 0
    fp: NonNegativeInt = 0
    fn: NonNegativeInt = 0
    tn: NonNegativeInt = 0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    def add(self, predicted_positive: bool, reference_positive: bool) -> "ConfusionMatrix":
        field = {
            (True, True): "tp",
            (True, False): "fp",
            (False, True): "fn",
            (False, False): "tn",
        }[(predicted_positive, reference_positive)]
        return self.updated(**{field: getattr(self, field) + 1})


class BinaryAgreement(DomainModel):
    matrix: ConfusionMatrix
    agreement: Estimate
    precision: Estimate
    recall: Estimate
    f1: Estimate
    kappa: Estimate
    pabak: Estimate = Field(description="Prevalence- and bias-adjusted kappa: 2 p_o - 1.")
    reference_positive_rate: Estimate = Field(description="Prevalence of 'fail' (reference).")
    predicted_positive_rate: Estimate


def kappa_from_counts(
    both_pos: int, a_only: int, b_only: int, both_neg: int, *, min_ci: int
) -> Estimate:
    """Cohen's kappa for two raters on a binary label (a = first rater, b = second)."""
    n = both_pos + a_only + b_only + both_neg
    if n == 0:
        return Estimate.undefined("no items rated by both", 0)
    p_o = (both_pos + both_neg) / n
    a_pos, b_pos = (both_pos + a_only) / n, (both_pos + b_only) / n
    p_e = a_pos * b_pos + (1 - a_pos) * (1 - b_pos)
    if math.isclose(p_e, 1.0):
        return Estimate.undefined(
            "chance agreement is 1 (both raters used a single label); kappa is undefined", n
        )
    kappa = (p_o - p_e) / (1 - p_e)
    if n < min_ci:
        return Estimate(value=kappa, n=n, note=f"no interval: n < {min_ci}")
    se = math.sqrt(p_o * (1 - p_o) / (n * (1 - p_e) ** 2))
    return Estimate(
        value=kappa,
        n=n,
        ci_low=max(-1.0, kappa - Z_95 * se),
        ci_high=min(1.0, kappa + Z_95 * se),
        note="approximate large-sample interval",
    )


def binary_agreement(matrix: ConfusionMatrix, *, min_ci: int) -> BinaryAgreement:
    tp, fp, fn, tn, n = matrix.tp, matrix.fp, matrix.fn, matrix.tn, matrix.n
    precision = proportion(
        tp, tp + fp, min_ci=min_ci, if_empty="the evaluator predicted no failures"
    )
    recall = proportion(tp, tp + fn, min_ci=min_ci, if_empty="the reference has no failures")
    if precision.value is None or recall.value is None:
        f1 = Estimate.undefined("precision or recall is undefined", n)
    elif precision.value + recall.value == 0:
        f1 = Estimate(value=0.0, n=n, note="no interval computed for F1")
    else:
        f1 = Estimate(
            value=2 * precision.value * recall.value / (precision.value + recall.value),
            n=n,
            note="no interval computed for F1",
        )
    agreement = proportion(tp + tn, n, min_ci=min_ci, if_empty="no comparable items")
    pabak = (
        Estimate(value=2 * agreement.value - 1, n=n)
        if agreement.value is not None
        else Estimate.undefined("no comparable items", 0)
    )
    return BinaryAgreement(
        matrix=matrix,
        agreement=agreement,
        precision=precision,
        recall=recall,
        f1=f1,
        kappa=kappa_from_counts(tp, fp, fn, tn, min_ci=min_ci),
        pabak=pabak,
        reference_positive_rate=proportion(tp + fn, n, min_ci=min_ci, if_empty="no items"),
        predicted_positive_rate=proportion(tp + fp, n, min_ci=min_ci, if_empty="no items"),
    )


# --- reference labels ----------------------------------------------------------------------------


class ReferenceStatus(StrEnum):
    DEFINITE = "definite"
    DISPUTED = "disputed"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENTLY_SPECIFIED = "insufficiently_specified"


def reference_label(reviews: Iterable[HumanReview]) -> tuple[ReferenceStatus, ReviewLabel | None]:
    labels = [r.label for r in reviews]
    definite = {label for label in labels if label.definite}
    if len(definite) > 1:
        return ReferenceStatus.DISPUTED, None
    if ReviewLabel.INSUFFICIENTLY_SPECIFIED in labels:
        return ReferenceStatus.INSUFFICIENTLY_SPECIFIED, None
    if ReviewLabel.AMBIGUOUS in labels:
        return ReferenceStatus.AMBIGUOUS, None
    (label,) = definite
    return ReferenceStatus.DEFINITE, label


def category_reference(reviews: Iterable[HumanReview], category: FailureLabel) -> bool | None:
    """True/False if every reviewer who assessed the category says present/absent; else None."""
    verdicts = {r.categories[category] for r in reviews if category in r.categories}
    if verdicts == {CategoryVerdict.PRESENT}:
        return True
    if verdicts == {CategoryVerdict.ABSENT}:
        return False
    return None


# --- report ------------------------------------------------------------------------------------


class SampleSummary(DomainModel):
    reviewed_runs: NonNegativeInt = Field(description="Runs with at least one current review.")
    reviews: NonNegativeInt = Field(description="All review records, including corrections.")
    corrections: NonNegativeInt
    reviewers: list[str]
    definite: NonNegativeInt
    disputed: NonNegativeInt
    ambiguous: NonNegativeInt
    insufficiently_specified: NonNegativeInt
    automated_inconclusive: NonNegativeInt = Field(
        description="Definite-reference runs where the evaluator abstained (inconclusive)."
    )
    excluded_not_blind: NonNegativeInt
    missing_evaluation: NonNegativeInt
    compared: NonNegativeInt = Field(description="Runs in the automated-vs-human matrix.")
    synthetic_reviews: NonNegativeInt


class CategoryAgreement(DomainModel):
    category: FailureLabel
    agreement: BinaryAgreement
    disagreements: list[str] = Field(description="Run IDs where evaluator and reference differ.")
    human_assessed_runs: NonNegativeInt


class ReviewerPairAgreement(DomainModel):
    reviewer_a: NonEmptyStr
    reviewer_b: NonEmptyStr
    both_reviewed: NonNegativeInt
    both_definite: NonNegativeInt
    one_or_both_not_definite: NonNegativeInt
    agreement: Estimate
    kappa: Estimate
    disagreements: list[str]


class CalibrationReport(DomainModel):
    evaluator_version: NonEmptyStr
    scope: Literal["sampled", "all", "custom"]
    empirical: bool = Field(
        description="True only if every review is a real human label (no synthetic fixtures)."
    )
    summary: SampleSummary
    automated_vs_human: BinaryAgreement
    automated_vs_human_disagreements: list[str]
    categories: list[CategoryAgreement]
    reviewer_pairs: list[ReviewerPairAgreement]
    caveats: list[str]


def calibration_report(
    evaluations: Mapping[str, CaseEvaluation],
    reviews: list[HumanReview],
    *,
    evaluator_version: str,
    run_ids: set[str] | None = None,
    scope: Literal["sampled", "all", "custom"] = "custom",
    require_blind: bool = True,
    min_sample_for_ci: int = DEFAULT_MIN_SAMPLE_FOR_CI,
) -> CalibrationReport:
    """Compare evaluations (by run ID) with human reviews of the same evaluator version.

    ``run_ids`` restricts the population (e.g. to the random calibration sample)."""
    in_scope = [
        r
        for r in reviews
        if r.evaluator_version == evaluator_version and (run_ids is None or r.run_id in run_ids)
    ]
    current = current_reviews(in_scope)
    by_run: dict[str, list[HumanReview]] = {}
    for review in current:
        by_run.setdefault(review.run_id, []).append(review)

    counts = dict.fromkeys(ReferenceStatus, 0)
    matrix = ConfusionMatrix()
    disagreements: list[str] = []
    abstained = not_blind = missing = 0
    comparable_runs: list[str] = []
    for run_id in sorted(by_run):
        run_reviews = by_run[run_id]
        status, label = reference_label(run_reviews)
        counts[status] += 1
        evaluation = evaluations.get(run_id)
        if evaluation is None:
            missing += 1
            continue
        if evaluation.evaluator_version != evaluator_version:
            raise ValueError(f"evaluation of '{run_id}' is from another evaluator version")
        if require_blind and not all(r.blind for r in run_reviews):
            not_blind += 1
            continue
        comparable_runs.append(run_id)
        if status is not ReferenceStatus.DEFINITE:
            continue
        if evaluation.verdict is Verdict.INCONCLUSIVE:
            abstained += 1
            continue
        predicted_fail = evaluation.verdict is Verdict.FAILED
        reference_fail = label is ReviewLabel.FAIL
        matrix = matrix.add(predicted_fail, reference_fail)
        if predicted_fail != reference_fail:
            disagreements.append(run_id)

    categories = []
    for category in REVIEWABLE_CATEGORIES:
        cat_matrix = ConfusionMatrix()
        cat_disagreements: list[str] = []
        assessed = 0
        for run_id in comparable_runs:
            reference = category_reference(by_run[run_id], category)
            if reference is None:
                continue
            assessed += 1
            predicted = category in evaluations[run_id].labels
            cat_matrix = cat_matrix.add(predicted, reference)
            if predicted != reference:
                cat_disagreements.append(run_id)
        if assessed:
            categories.append(
                CategoryAgreement(
                    category=category,
                    agreement=binary_agreement(cat_matrix, min_ci=min_sample_for_ci),
                    disagreements=cat_disagreements,
                    human_assessed_runs=assessed,
                )
            )

    summary = SampleSummary(
        reviewed_runs=len(by_run),
        reviews=len(in_scope),
        corrections=sum(1 for r in in_scope if r.supersedes is not None),
        reviewers=sorted({r.reviewer for r in in_scope}),
        definite=counts[ReferenceStatus.DEFINITE],
        disputed=counts[ReferenceStatus.DISPUTED],
        ambiguous=counts[ReferenceStatus.AMBIGUOUS],
        insufficiently_specified=counts[ReferenceStatus.INSUFFICIENTLY_SPECIFIED],
        automated_inconclusive=abstained,
        excluded_not_blind=not_blind,
        missing_evaluation=missing,
        compared=matrix.n,
        synthetic_reviews=sum(1 for r in in_scope if r.provenance == "synthetic"),
    )
    binary = binary_agreement(matrix, min_ci=min_sample_for_ci)
    report = CalibrationReport(
        evaluator_version=evaluator_version,
        scope=scope,
        empirical=summary.synthetic_reviews == 0 and summary.reviews > 0,
        summary=summary,
        automated_vs_human=binary,
        automated_vs_human_disagreements=disagreements,
        categories=categories,
        reviewer_pairs=reviewer_pairs(by_run, min_ci=min_sample_for_ci),
        caveats=[],
    )
    return report.updated(caveats=caveats(report, min_sample_for_ci))


def reviewer_pairs(
    by_run: Mapping[str, list[HumanReview]], *, min_ci: int
) -> list[ReviewerPairAgreement]:
    reviewers = sorted({r.reviewer for reviews in by_run.values() for r in reviews})
    pairs = []
    for a, b in combinations(reviewers, 2):
        both = not_definite = 0
        counts = [0, 0, 0, 0]  # both fail, a fail only, b fail only, both pass
        disagreements: list[str] = []
        for run_id, reviews in sorted(by_run.items()):
            labels = {r.reviewer: r.label for r in reviews}
            if a not in labels or b not in labels:
                continue
            both += 1
            la, lb = labels[a], labels[b]
            if not (la.definite and lb.definite):
                not_definite += 1
                continue
            index = {
                (True, True): 0,
                (True, False): 1,
                (False, True): 2,
                (False, False): 3,
            }[(la is ReviewLabel.FAIL, lb is ReviewLabel.FAIL)]
            counts[index] += 1
            if la != lb:
                disagreements.append(run_id)
        if both == 0:
            continue
        definite = sum(counts)
        pairs.append(
            ReviewerPairAgreement(
                reviewer_a=a,
                reviewer_b=b,
                both_reviewed=both,
                both_definite=definite,
                one_or_both_not_definite=not_definite,
                agreement=proportion(
                    counts[0] + counts[3],
                    definite,
                    min_ci=min_ci,
                    if_empty="no shared definite labels",
                ),
                kappa=kappa_from_counts(*counts, min_ci=min_ci),
                disagreements=disagreements,
            )
        )
    return pairs


def caveats(report: CalibrationReport, min_ci: int) -> list[str]:
    notes = []
    summary, binary = report.summary, report.automated_vs_human
    if not report.empirical:
        notes.append(
            "Not an empirical calibration: "
            + (
                f"{summary.synthetic_reviews} of {summary.reviews} reviews are synthetic fixtures."
                if summary.reviews
                else "there are no reviews."
            )
        )
    if report.scope != "sampled":
        notes.append(
            "Population is not (only) the random calibration sample; queue-selected runs "
            "(uncertain, disputed, security-sensitive) over-represent hard cases, so error rates "
            "are biased estimates of the evaluator's overall behaviour."
        )
    if binary.matrix.n < min_ci:
        notes.append(
            f"Only {binary.matrix.n} compared runs (< {min_ci}): no intervals; treat as anecdotal."
        )
    prevalence = binary.reference_positive_rate.value
    if prevalence is not None and (prevalence < 0.1 or prevalence > 0.9):
        notes.append(
            f"Class imbalance (reference failure rate {prevalence:.0%}): high raw agreement can "
            "coexist with low kappa; compare kappa with PABAK and look at precision/recall."
        )
    excluded = summary.disputed + summary.ambiguous + summary.insufficiently_specified
    if excluded:
        notes.append(
            f"{excluded} reviewed run(s) have no definite human reference (disputed, ambiguous, "
            "or insufficiently specified) and are excluded; metrics describe only clear cases."
        )
    if summary.excluded_not_blind:
        notes.append(
            f"{summary.excluded_not_blind} run(s) excluded because a reviewer saw the automated "
            "verdict (not independent)."
        )
    if summary.automated_inconclusive:
        notes.append(
            f"The evaluator abstained (inconclusive) on {summary.automated_inconclusive} run(s) "
            "with a definite human label; these are outside the confusion matrix."
        )
    return notes
