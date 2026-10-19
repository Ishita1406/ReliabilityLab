"""Synthetic experiment results with exactly specified verdicts, for comparison/policy tests.

Evaluations are real, validated ``CaseEvaluation`` objects (so their verdict always agrees with
their criteria); only the runs behind them are synthetic.
"""

from collections.abc import Mapping, Sequence

from app.agents.interface import ModelUsage
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
)
from app.evaluation.scoring import decide
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.records import ExperimentResults, RunRecord
from app.experiments.samples import BASELINE_VERSION, EVALUATION_SUITE, sample_configuration

CASE = EVALUATION_SUITE.cases[0].id
SCENARIOS = [s.id for s in EVALUATION_SUITE.scenarios]

P, F, U = "pass", "fail", "unsure"
"""Run outcomes: passed, failed (task_correctness), inconclusive."""


def criterion(
    cid: str,
    status: CriterionStatus,
    *,
    ctype: CriterionType = CriterionType.DECLARED_OUTCOME,
    labels: list[FailureLabel] | None = None,
) -> CriterionEvaluation:
    default = {
        CriterionStatus.PASSED: [],
        CriterionStatus.FAILED: [FailureLabel.TASK_CORRECTNESS],
        CriterionStatus.INCONCLUSIVE: [FailureLabel.EVALUATOR_UNCERTAINTY],
    }[status]
    layer = (
        EvaluationLayer.TOOL_BEHAVIOR
        if ctype is CriterionType.PROHIBITED_ACTIONS
        else EvaluationLayer.ANSWER
    )
    return CriterionEvaluation(
        criterion_id=cid,
        criterion_type=ctype,
        layer=layer,
        evaluator=EvaluatorKind.DETERMINISTIC,
        status=status,
        weight=1.0,
        required=True,
        explanation="synthetic",
        labels=default if labels is None else labels,
    )


def evaluation(
    run_id: str,
    scenario_id: str,
    outcome: str,
    *,
    evaluator_version: str,
    unauthorized: bool | None = None,
) -> CaseEvaluation:
    """``unauthorized``: None -> no prohibited_actions criterion; True/False -> failed/passed."""
    status = {
        P: CriterionStatus.PASSED,
        F: CriterionStatus.FAILED,
        U: CriterionStatus.INCONCLUSIVE,
    }[outcome]
    criteria = [criterion("outcome", status)]
    if unauthorized is not None:
        criteria.append(
            criterion(
                "no-unauthorized",
                CriterionStatus.FAILED if unauthorized else CriterionStatus.PASSED,
                ctype=CriterionType.PROHIBITED_ACTIONS,
                labels=[FailureLabel.SECURITY_VIOLATION] if unauthorized else [],
            )
        )
    decision = decide(criteria, 1.0)
    labels: dict[FailureLabel, list[str]] = {}
    for item in criteria:
        for label in item.labels:
            labels.setdefault(label, []).append(item.criterion_id)
    return CaseEvaluation(
        case_id=CASE,
        scenario_id=scenario_id,
        run_id=run_id,
        run_status=RunStatus.COMPLETED,
        evaluator_version=evaluator_version,
        verdict=decision.verdict,
        score=decision.score,
        score_upper_bound=decision.upper_bound,
        pass_threshold=1.0,
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
            for label, ids in labels.items()
        ],
        explanation="synthetic",
    )


def results(
    name: str,
    outcomes: Mapping[str, Sequence[str | None]],
    *,
    configuration: ExperimentConfiguration | None = None,
    unauthorized: Mapping[str, bool] | None = None,
    durations: Mapping[str, float] | None = None,
    usage: list[ModelUsage] | None = None,
) -> ExperimentResults:
    """``outcomes[scenario] = [outcome per repetition]``; ``None`` = run present, unevaluated;
    a shorter list than the planned repetitions = missing runs."""
    repetitions = max(len(v) for v in outcomes.values())
    configuration = configuration or sample_configuration(
        BASELINE_VERSION, scenario_ids=list(outcomes), repetitions=repetitions
    )
    experiment_id = derived_id(IdPrefix.EXPERIMENT, "synthetic", name)
    records = []
    for scenario_id, per_rep in outcomes.items():
        for repetition, outcome in enumerate(per_rep):
            run_id = derived_id(IdPrefix.RUN, experiment_id, scenario_id, str(repetition))
            records.append(
                RunRecord(
                    run_id=run_id,
                    case_id=CASE,
                    scenario_id=scenario_id,
                    repetition=repetition,
                    status=RunStatus.COMPLETED,
                    duration_seconds=(durations or {}).get(scenario_id, 1.0),
                    usage=usage,
                    evaluation=None
                    if outcome is None
                    else evaluation(
                        run_id,
                        scenario_id,
                        outcome,
                        evaluator_version=configuration.evaluator_version,
                        unauthorized=(unauthorized or {}).get(scenario_id),
                    ),
                )
            )
    return ExperimentResults(experiment_id=experiment_id, configuration=configuration, runs=records)


def uniform(outcome: str, count: int, repetitions: int = 1) -> dict[str, list[str | None]]:
    """``count`` scenarios, each with ``repetitions`` runs of ``outcome``."""
    return {s: [outcome] * repetitions for s in SCENARIOS[:count]}
