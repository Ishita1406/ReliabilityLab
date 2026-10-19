"""Evaluation results and failure classifications.

An ``EvaluationResult`` records how one run scored against its scenario's effective
expectation. Criteria are binary (pass/fail); the score is the weighted fraction of passed
criteria. ``score`` and ``passed`` are stored (so results survive a JSON round trip and
later scoring-rule changes) and are validated against ``criterion_results`` so they can
never disagree with them.

A ``FailureClassification`` labels a run with a ``FailureCategory``. Rule-based
classifications come from an evaluation; humans may also classify a run directly.
"""

import math
from typing import Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.enums import ClassificationSource, CriterionType, FailureCategory, Severity
from app.domain.ids import (
    EvaluationResultId,
    FailureClassificationId,
    IdPrefix,
    RunId,
    Slug,
    TraceEventId,
    new_id,
)
from app.domain.timestamps import UtcDateTime, utc_now


class CriterionResult(DomainModel):
    criterion_id: Slug
    criterion_type: CriterionType
    passed: bool
    weight: float = Field(gt=0.0)
    required: bool
    detail: NonEmptyStr | None = Field(default=None, description="Why it passed or failed.")


def weighted_score(results: list[CriterionResult]) -> float:
    """Sum of weights of passed criteria divided by the sum of all weights."""
    total = sum(r.weight for r in results)
    return sum(r.weight for r in results if r.passed) / total


def is_passing(results: list[CriterionResult], pass_threshold: float) -> bool:
    """All required criteria pass and the weighted score meets the threshold."""
    return all(r.passed for r in results if r.required) and (
        weighted_score(results) >= pass_threshold
    )


class EvaluationResult(DomainModel):
    id: EvaluationResultId = Field(default_factory=lambda: new_id(IdPrefix.EVALUATION_RESULT))
    run_id: RunId
    evaluator_version: NonEmptyStr = Field(description="Version of the scoring implementation.")
    criterion_results: list[CriterionResult] = Field(min_length=1)
    pass_threshold: float = Field(gt=0.0, le=1.0, description="Copied from the scoring policy.")
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    created_at: UtcDateTime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique((r.criterion_id for r in self.criterion_results), "criterion ids")
        expected_score = weighted_score(self.criterion_results)
        if not math.isclose(self.score, expected_score, abs_tol=1e-9):
            raise ValueError(
                f"score {self.score} does not match weighted criterion score {expected_score}"
            )
        if self.passed != is_passing(self.criterion_results, self.pass_threshold):
            raise ValueError("passed does not match criterion results and pass_threshold")
        return self


class FailureClassification(DomainModel):
    id: FailureClassificationId = Field(
        default_factory=lambda: new_id(IdPrefix.FAILURE_CLASSIFICATION)
    )
    run_id: RunId
    evaluation_result_id: EvaluationResultId | None = None
    category: FailureCategory
    severity: Severity
    source: ClassificationSource
    rationale: NonEmptyStr
    evidence_event_ids: list[TraceEventId] = Field(
        default_factory=list, description="Trace events that support the classification."
    )
    created_at: UtcDateTime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique(self.evidence_event_ids, "evidence_event_ids")
        if self.source is ClassificationSource.RULE and self.evaluation_result_id is None:
            raise ValueError("rule-based classifications must reference an evaluation result")
        return self
