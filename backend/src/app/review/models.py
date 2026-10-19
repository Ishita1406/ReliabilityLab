"""Human review records (append-only) and their correction history.

A ``HumanReview`` is one reviewer's judgment of one run, as evaluated by one evaluator version.
Records are never edited. A reviewer who changes their mind submits a new record that
``supersedes`` their previous one and says why; the chain of records per (run, evaluator
version, reviewer) is the auditable history, and its last record is the reviewer's *current*
judgment. Different reviewers' chains are independent, so their original judgments are kept.

Labels:

* ``pass`` / ``fail``: definite judgments of whether the run met the task.
* ``ambiguous``: the reviewer cannot decide because the outcome is genuinely debatable.
* ``insufficiently_specified``: the benchmark case does not define what correct means here.

The last two are kept apart from definite labels everywhere: they never count as pass or fail.

Categories use the evaluation engine's ``FailureLabel`` vocabulary. A reviewer states, per
category they assessed, whether that failure is ``present``, ``absent``, or ``unsure``;
categories not listed were not assessed (which is not the same as absent).
``evaluator_uncertainty`` describes the evaluator, not the agent, and cannot be assigned.
"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.ids import HumanReviewId, IdPrefix, RunId, Slug, TraceEventId, new_id
from app.domain.timestamps import UtcDateTime, utc_now
from app.evaluation.models import FailureLabel


class ReviewLabel(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENTLY_SPECIFIED = "insufficiently_specified"

    @property
    def definite(self) -> bool:
        return self in (ReviewLabel.PASS, ReviewLabel.FAIL)


class CategoryVerdict(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNSURE = "unsure"


class ReviewEvidence(DomainModel):
    """What the reviewer relied on: a trace event and/or a note, optionally a verbatim quote."""

    event_id: TraceEventId | None = None
    note: NonEmptyStr
    excerpt: str | None = Field(default=None, max_length=1000)


Provenance = Literal["human", "synthetic"]


class HumanReview(DomainModel):
    id: HumanReviewId = Field(default_factory=lambda: new_id(IdPrefix.HUMAN_REVIEW))
    run_id: RunId
    case_id: Slug
    scenario_id: Slug
    evaluator_version: NonEmptyStr = Field(
        description="The automated evaluation this review is paired with for calibration."
    )
    reviewer: NonEmptyStr
    label: ReviewLabel
    categories: dict[FailureLabel, CategoryVerdict] = Field(
        default_factory=dict, description="Assessed categories only; missing = not assessed."
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Reviewer's own confidence in the label."
    )
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    rationale: NonEmptyStr | None = None
    blind: bool = Field(
        description="True if the reviewer did not see the automated verdict before labelling."
    )
    provenance: Provenance = Field(
        default="human", description="'synthetic' for fixtures; never mixed into empirical claims."
    )
    supersedes: HumanReviewId | None = Field(
        default=None, description="The same reviewer's previous record for this run."
    )
    correction_reason: NonEmptyStr | None = None
    created_at: UtcDateTime = Field(default_factory=utc_now, description="Annotation time.")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if FailureLabel.EVALUATOR_UNCERTAINTY in self.categories:
            raise ValueError("evaluator_uncertainty describes the evaluator, not the agent")
        if (self.supersedes is None) != (self.correction_reason is None):
            raise ValueError("a correction needs both supersedes and correction_reason")
        if self.supersedes == self.id:
            raise ValueError("a review cannot supersede itself")
        if not self.label.definite and self.rationale is None:
            raise ValueError(f"'{self.label}' labels need a rationale explaining why")
        return self

    @property
    def chain_key(self) -> tuple[str, str, str]:
        return (self.run_id, self.evaluator_version, self.reviewer)


class ReviewHistoryError(ValueError):
    """Records do not form valid append-only correction chains."""


def current_reviews(reviews: list[HumanReview]) -> list[HumanReview]:
    """The latest record of every (run, evaluator version, reviewer) chain.

    Validates that every chain is linear: exactly one root, every correction supersedes a
    record of the same chain, no record is superseded twice, and corrections are not older
    than what they correct.
    """
    by_id = {r.id: r for r in reviews}
    if len(by_id) != len(reviews):
        raise ReviewHistoryError("duplicate review ids")
    superseded: set[str] = set()
    roots: dict[tuple[str, str, str], str] = {}
    for review in reviews:
        if review.supersedes is None:
            if review.chain_key in roots:
                raise ReviewHistoryError(
                    f"reviewer '{review.reviewer}' has two independent reviews of run "
                    f"'{review.run_id}'; the second must supersede the first"
                )
            roots[review.chain_key] = review.id
            continue
        previous = by_id.get(review.supersedes)
        if previous is None:
            raise ReviewHistoryError(
                f"'{review.id}' supersedes unknown review '{review.supersedes}'"
            )
        if previous.chain_key != review.chain_key:
            raise ReviewHistoryError("a correction must be by the same reviewer, for the same run")
        if review.supersedes in superseded:
            raise ReviewHistoryError(f"review '{review.supersedes}' is superseded twice")
        if review.created_at < previous.created_at:
            raise ReviewHistoryError("a correction cannot be older than the review it corrects")
        superseded.add(review.supersedes)
    heads = [r for r in reviews if r.id not in superseded]
    # Each chain must be one path from its head back to its root covering all its records;
    # this rejects cycles (A supersedes B supersedes A) that the local checks cannot see.
    sizes: dict[tuple[str, str, str], int] = {}
    for review in reviews:
        sizes[review.chain_key] = sizes.get(review.chain_key, 0) + 1
    covered: dict[tuple[str, str, str], int] = {}
    for head in heads:
        if head.chain_key in covered:
            raise ReviewHistoryError("a chain has more than one current review")
        covered[head.chain_key] = len(history(reviews, head))
    if covered != sizes:
        raise ReviewHistoryError("correction chains contain a cycle or unreachable records")
    return heads


def history(reviews: list[HumanReview], head: HumanReview) -> list[HumanReview]:
    """The full chain ending at ``head``, oldest first."""
    by_id = {r.id: r for r in reviews}
    chain = [head]
    while chain[-1].supersedes is not None:
        chain.append(by_id[chain[-1].supersedes])
    return list(reversed(chain))
