"""Scoring rules.

The Milestone 2 rule is kept: a criterion contributes its weight when it passed, and a run
passes when every required criterion passes and ``score >= pass_threshold``. Rubric criteria
take part as passed/failed too; their graded score stays on the criterion result.

Inconclusive criteria are handled by bounds instead of guesses:

* ``score`` counts them as not passed (lower bound); ``upper_bound`` counts them as passed.
* ``failed``  if a required criterion failed, or even the upper bound misses the threshold.
* ``passed``  if no required criterion is failed or inconclusive and the lower bound meets the
  threshold, i.e. the verdict would not change whatever the inconclusive criteria are.
* otherwise ``inconclusive``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.evaluation.models import CriterionEvaluation, CriterionStatus, Verdict


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    score: float
    upper_bound: float


def decide(criteria: Sequence[CriterionEvaluation], pass_threshold: float) -> Decision:
    total = sum(c.weight for c in criteria)
    passed = sum(c.weight for c in criteria if c.status is CriterionStatus.PASSED)
    open_ = sum(c.weight for c in criteria if c.status is CriterionStatus.INCONCLUSIVE)
    score, upper = passed / total, (passed + open_) / total

    required = [c for c in criteria if c.required]
    if any(c.status is CriterionStatus.FAILED for c in required) or upper < pass_threshold:
        verdict = Verdict.FAILED
    elif all(c.status is CriterionStatus.PASSED for c in required) and score >= pass_threshold:
        verdict = Verdict.PASSED
    else:
        verdict = Verdict.INCONCLUSIVE
    return Decision(verdict=verdict, score=score, upper_bound=upper)


def layer_decision(
    criteria: Sequence[CriterionEvaluation],
) -> tuple[Verdict, float | None]:
    """Per-layer verdict: failed if any criterion failed, inconclusive if any is, else passed.
    The score is the weighted pass fraction over decided criteria (``None`` if none)."""
    decided = [c for c in criteria if c.status is not CriterionStatus.INCONCLUSIVE]
    score = (
        sum(c.weight for c in decided if c.status is CriterionStatus.PASSED)
        / sum(c.weight for c in decided)
        if decided
        else None
    )
    statuses = {c.status for c in criteria}
    if CriterionStatus.FAILED in statuses:
        return Verdict.FAILED, score
    if CriterionStatus.INCONCLUSIVE in statuses:
        return Verdict.INCONCLUSIVE, score
    return Verdict.PASSED, score
