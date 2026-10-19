"""Review queue: which runs need human review, why, and how many independent reviews.

Reasons (highest priority first):

* ``security_sensitive`` - the evaluation carries ``security_violation``, a
  ``prohibited_actions`` criterion did not pass, or the scenario injects adversarial input
  (prompt-injection faults). A missed security failure is the costliest evaluator error.
* ``disputed`` - reviewers' current definite labels conflict, or a definite human consensus
  contradicts the automated verdict.
* ``uncertain`` - the automated verdict, or any criterion, is inconclusive.
* ``sampled`` - selected by the random calibration sample (see below).

**Sampling.** Calibration metrics need an *unbiased* sample: the other reasons deliberately
pick hard cases, which would make the evaluator look worse (or, for disputes, different) than
it is overall. Each run is sampled independently with probability ``rate`` by a hash of
(seed, run ID): reproducible, independent of run order, and independent of the verdict.
Sampled items are marked ``blind``: reviewers should label them without seeing the automated
verdict, so that their labels are independent of it.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from app.domain.base import DomainModel
from app.domain.benchmark import BenchmarkSuite
from app.domain.enums import CriterionType, FaultType
from app.domain.ids import RunId, Slug
from app.evaluation.models import CaseEvaluation, CriterionStatus, FailureLabel, Verdict
from app.experiments.records import ExperimentResults
from app.review.agreement import ReferenceStatus, reference_label
from app.review.models import HumanReview, ReviewLabel, current_reviews
from app.runner.fault_injection import MAX_SAFE_SEED_BITS, derive_seed


class QueueReason(StrEnum):
    SECURITY_SENSITIVE = "security_sensitive"
    DISPUTED = "disputed"
    UNCERTAIN = "uncertain"
    SAMPLED = "sampled"


PRIORITY = {reason: rank for rank, reason in enumerate(QueueReason)}


class SamplingPolicy(DomainModel):
    rate: float = Field(default=0.2, gt=0.0, le=1.0)
    seed: NonNegativeInt = 0


def is_sampled(run_id: str, policy: SamplingPolicy) -> bool:
    draw = derive_seed(policy.seed, "calibration-sample", run_id) / float(2**MAX_SAFE_SEED_BITS)
    return draw < policy.rate


class QueuePolicy(DomainModel):
    sampling: SamplingPolicy = Field(default_factory=SamplingPolicy)
    reviews_required: dict[QueueReason, PositiveInt] = Field(
        default_factory=lambda: {
            QueueReason.SECURITY_SENSITIVE: 2,
            QueueReason.DISPUTED: 3,  # two conflicting reviews plus an adjudicating third
            QueueReason.UNCERTAIN: 1,
            QueueReason.SAMPLED: 2,  # two reviewers per sampled run allow reviewer agreement
        }
    )

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if set(self.reviews_required) != set(QueueReason):
            raise ValueError("reviews_required must cover every queue reason")
        return self


class QueueItem(DomainModel):
    run_id: RunId
    case_id: Slug
    scenario_id: Slug
    evaluator_version: str
    automated_verdict: Verdict
    reasons: list[QueueReason] = Field(min_length=1)
    priority: NonNegativeInt = Field(description="0 = most urgent (first reason's rank).")
    required_reviews: PositiveInt
    current_reviewers: list[str]
    blind: bool = Field(description="Reviewers should not see the automated verdict.")

    @property
    def open(self) -> bool:
        return len(self.current_reviewers) < self.required_reviews


def security_scenarios(suite: BenchmarkSuite) -> set[str]:
    """Scenarios that feed adversarial content to the agent."""
    return {
        s.id for s in suite.scenarios if any(f.type is FaultType.PROMPT_INJECTION for f in s.faults)
    }


def _security_sensitive(evaluation: CaseEvaluation, scenario_is_adversarial: bool) -> bool:
    return (
        scenario_is_adversarial
        or FailureLabel.SECURITY_VIOLATION in evaluation.labels
        or any(
            c.criterion_type is CriterionType.PROHIBITED_ACTIONS
            and c.status is not CriterionStatus.PASSED
            for c in evaluation.criteria
        )
    )


def _disputed(evaluation: CaseEvaluation, reviews: list[HumanReview]) -> bool:
    if not reviews:
        return False
    status, label = reference_label(reviews)
    if status is ReferenceStatus.DISPUTED:
        return True
    if status is ReferenceStatus.DEFINITE and evaluation.verdict is not Verdict.INCONCLUSIVE:
        return (label is ReviewLabel.FAIL) != (evaluation.verdict is Verdict.FAILED)
    return False


def _uncertain(evaluation: CaseEvaluation) -> bool:
    return evaluation.verdict is Verdict.INCONCLUSIVE or any(
        c.status is CriterionStatus.INCONCLUSIVE for c in evaluation.criteria
    )


def build_queue(
    results: ExperimentResults,
    reviews: Iterable[HumanReview],
    policy: QueuePolicy | None = None,
    *,
    adversarial_scenarios: set[str] | None = None,
) -> list[QueueItem]:
    """Every evaluated run that needs review for at least one reason; open items first."""
    policy = policy or QueuePolicy()
    adversarial = adversarial_scenarios or set()
    by_run: dict[str, list[HumanReview]] = {}
    for review in current_reviews(list(reviews)):
        by_run.setdefault(review.run_id, []).append(review)

    items = []
    for record in results.runs:
        evaluation = record.evaluation
        if evaluation is None:
            continue
        run_reviews = [
            r
            for r in by_run.get(record.run_id, [])
            if r.evaluator_version == evaluation.evaluator_version
        ]
        flags = {
            QueueReason.SECURITY_SENSITIVE: _security_sensitive(
                evaluation, record.scenario_id in adversarial
            ),
            QueueReason.DISPUTED: _disputed(evaluation, run_reviews),
            QueueReason.UNCERTAIN: _uncertain(evaluation),
            QueueReason.SAMPLED: is_sampled(record.run_id, policy.sampling),
        }
        reasons = [reason for reason in QueueReason if flags[reason]]
        if not reasons:
            continue
        items.append(
            QueueItem(
                run_id=record.run_id,
                case_id=record.case_id,
                scenario_id=record.scenario_id,
                evaluator_version=evaluation.evaluator_version,
                automated_verdict=evaluation.verdict,
                reasons=reasons,
                priority=PRIORITY[reasons[0]],
                required_reviews=max(policy.reviews_required[r] for r in reasons),
                current_reviewers=sorted({r.reviewer for r in run_reviews}),
                blind=QueueReason.SAMPLED in reasons,
            )
        )
    items.sort(key=lambda item: (not item.open, item.priority, item.run_id))
    return items


def sampled_run_ids(results: ExperimentResults, policy: SamplingPolicy) -> set[str]:
    return {r.run_id for r in results.runs if is_sampled(r.run_id, policy)}
