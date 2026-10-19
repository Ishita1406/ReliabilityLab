"""The evaluation engine: scores one stored run against its scenario's expectation.

    evaluation = Evaluator(judge=None).evaluate(trace, case, scenario)

Steps:

1. Build a ``RunView`` from the persisted trace.
2. Run every criterion of the effective expectation: deterministic checks, or the judge for
   rubric criteria (inconclusive without one). Unless the expectation has an
   ``execution_limits`` criterion, add the implicit required ``implicit.run-completed`` check:
   a run that did not finish cannot pass.
3. Decide the verdict and score (``app.evaluation.scoring``), per layer and overall.
4. Collect failure labels from non-passed criteria, each with its criterion IDs and events.
5. Record every failed tool call and whether a later call to the same tool succeeded. Failed
   calls are observations; they affect the verdict only through criteria.
"""

from app.domain.benchmark import BenchmarkCase, Expectation, RubricCriterion, Scenario
from app.domain.enums import CriterionType
from app.evaluation.checks import (
    CHECKS,
    IMPLICIT_RUN_COMPLETED,
    CheckContext,
    check_run_completed,
    to_evaluation,
)
from app.evaluation.judge import LLMJudge, no_judge_result
from app.evaluation.models import (
    CaseEvaluation,
    CriterionEvaluation,
    CriterionStatus,
    EvaluationLayer,
    FailureLabel,
    LabelAssignment,
    LayerSummary,
    ToolFailureObservation,
    Verdict,
)
from app.evaluation.run_view import RunView
from app.evaluation.scoring import decide, layer_decision
from app.tracing.schema import CollectedTrace

EVALUATOR_VERSION = "0.1.0"


class EvaluationInputError(ValueError):
    """The trace, case, and scenario do not belong together."""


def effective_expectation(case: BenchmarkCase, scenario: Scenario) -> Expectation:
    return scenario.expectation_override or case.expectation


class Evaluator:
    def __init__(self, judge: LLMJudge | None = None) -> None:
        self._judge = judge

    @property
    def version(self) -> str:
        base = f"evaluator-{EVALUATOR_VERSION}"
        return f"{base}+{self._judge.version}" if self._judge else base

    def evaluate(
        self, trace: CollectedTrace, case: BenchmarkCase, scenario: Scenario
    ) -> CaseEvaluation:
        view = RunView.from_trace(trace)
        if scenario.case_id != case.id:
            raise EvaluationInputError(f"scenario '{scenario.id}' is not a scenario of '{case.id}'")
        if view.scenario_id != scenario.id:
            raise EvaluationInputError(
                f"trace of run '{view.run_id}' is for scenario '{view.scenario_id}', "
                f"not '{scenario.id}'"
            )
        expectation = effective_expectation(case, scenario)
        context = CheckContext(view=view, case=case)

        criteria: list[CriterionEvaluation] = []
        for criterion in expectation.evaluation_criteria:
            if isinstance(criterion, RubricCriterion):
                criteria.append(
                    self._judge.evaluate(criterion, view, case, expectation)
                    if self._judge
                    else no_judge_result(criterion)
                )
                continue
            outcome = CHECKS[criterion.type](criterion, context)
            criteria.append(
                to_evaluation(
                    criterion_id=criterion.id,
                    criterion_type=criterion.type,
                    weight=criterion.weight,
                    required=criterion.required,
                    outcome=outcome,
                )
            )
        if not any(
            c.type is CriterionType.EXECUTION_LIMITS for c in expectation.evaluation_criteria
        ):
            if any(c.criterion_id == IMPLICIT_RUN_COMPLETED for c in criteria):
                raise EvaluationInputError(f"criterion id '{IMPLICIT_RUN_COMPLETED}' is reserved")
            criteria.append(
                to_evaluation(
                    criterion_id=IMPLICIT_RUN_COMPLETED,
                    criterion_type=CriterionType.EXECUTION_LIMITS,
                    weight=1.0,
                    required=True,
                    outcome=check_run_completed(context),
                    implicit=True,
                )
            )

        threshold = expectation.scoring.pass_threshold
        decision = decide(criteria, threshold)
        judged = [c.confidence for c in criteria if c.confidence is not None]
        labels = _labels(criteria)
        return CaseEvaluation(
            case_id=case.id,
            scenario_id=scenario.id,
            run_id=view.run_id,
            run_status=view.run_status,
            evaluator_version=self.version,
            verdict=decision.verdict,
            score=decision.score,
            score_upper_bound=decision.upper_bound,
            pass_threshold=threshold,
            confidence=min(judged) if judged else None,
            criteria=criteria,
            layers=_layers(criteria),
            failure_labels=labels,
            tool_failures=tool_failure_observations(view),
            explanation=_explain(decision.verdict, decision.score, threshold, criteria),
        )


def _layers(criteria: list[CriterionEvaluation]) -> list[LayerSummary]:
    summaries = []
    for layer in EvaluationLayer:
        members = [c for c in criteria if c.layer is layer]
        if members:
            verdict, score = layer_decision(members)
            summaries.append(
                LayerSummary(
                    layer=layer,
                    verdict=verdict,
                    score=score,
                    criterion_ids=[c.criterion_id for c in members],
                )
            )
    return summaries


def _labels(criteria: list[CriterionEvaluation]) -> list[LabelAssignment]:
    grouped: dict[FailureLabel, list[CriterionEvaluation]] = {}
    for criterion in criteria:
        for label in criterion.labels:
            grouped.setdefault(label, []).append(criterion)
    assignments = []
    for label in FailureLabel:  # stable order
        members = grouped.get(label)
        if not members:
            continue
        event_ids: dict[str, None] = {}
        for criterion in members:
            for item in criterion.evidence:
                for event_id in item.event_ids:
                    event_ids.setdefault(event_id, None)
        assignments.append(
            LabelAssignment(
                label=label,
                criterion_ids=[c.criterion_id for c in members],
                event_ids=list(event_ids),
                rationale="; ".join(
                    f"{c.criterion_id} ({c.status}): {c.explanation}" for c in members
                ),
            )
        )
    return assignments


def tool_failure_observations(view: RunView) -> list[ToolFailureObservation]:
    observations = []
    for call in view.tool_calls:
        if call.succeeded:
            continue
        recovery = next(
            (
                later
                for later in view.tool_calls
                if later.tool_name == call.tool_name
                and later.sequence > call.sequence
                and later.succeeded
            ),
            None,
        )
        observations.append(
            ToolFailureObservation(
                event_id=call.event_id,
                result_event_id=call.result_event_id,
                tool_name=call.tool_name,
                status=call.status.value,
                error_type=call.error_type,
                fault_ids=[f.fault_id for f in call.faults],
                recovered=recovery is not None,
                recovered_by_event_id=recovery.event_id if recovery else None,
            )
        )
    return observations


def _explain(
    verdict: Verdict, score: float, threshold: float, criteria: list[CriterionEvaluation]
) -> str:
    failed = [c for c in criteria if c.status is CriterionStatus.FAILED]
    open_ = [c for c in criteria if c.status is CriterionStatus.INCONCLUSIVE]
    parts = [f"{verdict.upper()}: score {score:.2f} (threshold {threshold:.2f})."]
    required_failed = [c.criterion_id for c in failed if c.required]
    if required_failed:
        parts.append(f"Required criteria failed: {', '.join(required_failed)}.")
    optional_failed = [c.criterion_id for c in failed if not c.required]
    if optional_failed:
        parts.append(f"Optional criteria failed: {', '.join(optional_failed)}.")
    if open_:
        parts.append(f"Inconclusive: {', '.join(c.criterion_id for c in open_)}.")
    if not failed and not open_:
        parts.append("All criteria passed.")
    return " ".join(parts)
