"""Evaluation result models.

A ``CaseEvaluation`` scores one run against its scenario's effective expectation. Each
criterion yields a ``CriterionEvaluation`` whose *score*, *evidence*, and *explanation* are
separate fields: the score is what the evaluator decided, the evidence is what it observed
(with trace event IDs), and the explanation is its prose justification.

Criteria have three states. ``inconclusive`` means the evaluator could not decide (no judge,
an unreliable judge response, a truncated answer, a fault that never fired). Inconclusive is
never silently counted as passed or failed; see ``app.evaluation.scoring``.

Relationship to ``app.domain.evaluation.EvaluationResult`` (Milestone 2, persisted in
Milestone 5): that model is binary per criterion and has no evidence, confidence, or
inconclusive state. ``CaseEvaluation`` is not persisted yet (see the Milestone 6 docs).
"""

import math
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.enums import CriterionType, RunStatus
from app.domain.ids import RunId, Slug, TraceEventId
from app.domain.timestamps import UtcDateTime, utc_now


class CriterionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class Verdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class EvaluationLayer(StrEnum):
    """Layers are scored separately so that, e.g., a recovered tool timeout (tool behaviour)
    is never confused with a wrong answer."""

    ANSWER = "answer"
    TOOL_BEHAVIOR = "tool_behavior"
    EXECUTION = "execution"


class EvaluatorKind(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM_JUDGE = "llm_judge"


class FailureLabel(StrEnum):
    """Coarse failure categories assigned by the evaluation engine. A run may carry several.

    Distinct from the finer-grained ``app.domain.enums.FailureCategory`` used by persisted
    (Milestone 2/5) failure classifications; the docs record how they relate.
    """

    TASK_CORRECTNESS = "task_correctness"
    EVIDENCE_HANDLING = "evidence_handling"
    CONTEXT_RETENTION = "context_retention"
    TOOL_FAILURE = "tool_failure"
    SECURITY_VIOLATION = "security_violation"
    EXECUTION_LIMIT_VIOLATION = "execution_limit_violation"
    INVALID_OUTPUT = "invalid_output"
    RUN_ERROR = "run_error"
    EVALUATOR_UNCERTAINTY = "evaluator_uncertainty"


class EvidenceKind(StrEnum):
    ANSWER = "answer"
    """Text from (or a fact about) the final answer."""
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTEXT_AVAILABLE = "context_available"
    """Expected text that *was* present in a successful tool result the agent received."""
    CONTEXT_ABSENT = "context_absent"
    """Expected text that appeared in no successful tool result."""
    FAULT = "fault"
    RUN = "run"
    CITATION = "citation"
    SCHEMA = "schema"
    JUDGE = "judge"


class Evidence(DomainModel):
    """One observed fact supporting a decision. Never an inference."""

    kind: EvidenceKind
    statement: NonEmptyStr
    event_ids: list[TraceEventId] = Field(default_factory=list)
    excerpt: str | None = Field(default=None, description="Verbatim text, bounded.")


class CriterionEvaluation(DomainModel):
    criterion_id: Slug
    criterion_type: CriterionType
    layer: EvaluationLayer
    evaluator: EvaluatorKind
    implicit: bool = Field(
        default=False, description="Added by the engine, not listed in the expectation."
    )
    status: CriterionStatus
    weight: float = Field(gt=0.0)
    required: bool
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Graded score (rubrics; provisional when inconclusive). 1/0 for decided "
        "deterministic criteria.",
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Only for judged criteria."
    )
    evidence: list[Evidence] = Field(default_factory=list)
    explanation: NonEmptyStr
    labels: list[FailureLabel] = Field(
        default_factory=list, description="Labels this criterion contributes (not passed only)."
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status is CriterionStatus.PASSED and self.labels:
            raise ValueError("passed criteria carry no failure labels")
        if self.status is not CriterionStatus.PASSED and not self.labels:
            raise ValueError("failed or inconclusive criteria need at least one label")
        if self.status is CriterionStatus.INCONCLUSIVE and (
            FailureLabel.EVALUATOR_UNCERTAINTY not in self.labels
        ):
            raise ValueError("inconclusive criteria must be labelled evaluator_uncertainty")
        if self.evaluator is EvaluatorKind.DETERMINISTIC and self.confidence is not None:
            raise ValueError("deterministic criteria have no confidence")
        return self


class LabelAssignment(DomainModel):
    label: FailureLabel
    criterion_ids: list[Slug] = Field(min_length=1)
    event_ids: list[TraceEventId] = Field(default_factory=list)
    rationale: NonEmptyStr


class LayerSummary(DomainModel):
    layer: EvaluationLayer
    verdict: Verdict
    score: float | None = Field(ge=0.0, le=1.0)
    criterion_ids: list[Slug] = Field(min_length=1)


class ToolFailureObservation(DomainModel):
    """A tool call that did not succeed, and whether the run later recovered from it.

    Recorded for every failed call. A failed call is *not* a failure label by itself: it
    only counts against the run through criteria (e.g. ``expected_recovery``)."""

    event_id: TraceEventId
    result_event_id: TraceEventId
    tool_name: Slug
    status: NonEmptyStr
    error_type: str | None
    fault_ids: list[Slug] = Field(default_factory=list)
    recovered: bool
    recovered_by_event_id: TraceEventId | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.recovered != (self.recovered_by_event_id is not None):
            raise ValueError("recovered calls name the call that recovered them")
        return self


class CaseEvaluation(DomainModel):
    case_id: Slug
    scenario_id: Slug
    run_id: RunId
    run_status: RunStatus
    evaluator_version: NonEmptyStr
    verdict: Verdict
    score: float = Field(
        ge=0.0, le=1.0, description="Weight of passed criteria / total weight (conservative)."
    )
    score_upper_bound: float = Field(
        ge=0.0, le=1.0, description="Score if every inconclusive criterion had passed."
    )
    pass_threshold: float = Field(gt=0.0, le=1.0)
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Lowest judge confidence among judged criteria; null if none were judged.",
    )
    criteria: list[CriterionEvaluation] = Field(min_length=1)
    layers: list[LayerSummary]
    failure_labels: list[LabelAssignment] = Field(default_factory=list)
    tool_failures: list[ToolFailureObservation] = Field(default_factory=list)
    explanation: NonEmptyStr
    created_at: UtcDateTime = Field(default_factory=utc_now)

    @property
    def passed(self) -> bool | None:
        """``None`` when the verdict is inconclusive."""
        if self.verdict is Verdict.INCONCLUSIVE:
            return None
        return self.verdict is Verdict.PASSED

    @property
    def labels(self) -> list[FailureLabel]:
        return [assignment.label for assignment in self.failure_labels]

    def criterion(self, criterion_id: str) -> CriterionEvaluation:
        for item in self.criteria:
            if item.criterion_id == criterion_id:
                return item
        raise KeyError(criterion_id)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        # Imported here: scoring imports this module.
        from app.evaluation.scoring import decide

        require_unique((c.criterion_id for c in self.criteria), "criterion ids")
        require_unique((a.label for a in self.failure_labels), "failure labels")
        decision = decide(self.criteria, self.pass_threshold)
        if self.verdict is not decision.verdict:
            raise ValueError(f"verdict '{self.verdict}' contradicts criteria ({decision.verdict})")
        if not math.isclose(self.score, decision.score, abs_tol=1e-9) or not math.isclose(
            self.score_upper_bound, decision.upper_bound, abs_tol=1e-9
        ):
            raise ValueError("score does not match the criterion results")
        return self
