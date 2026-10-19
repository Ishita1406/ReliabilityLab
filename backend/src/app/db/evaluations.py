"""Persistence for run evaluations (``CaseEvaluation`` documents) and experiment results.

Evaluations are stored as full JSONB documents keyed by (run, evaluator version), next to the
Milestone 2/5 ``evaluation_results`` tables, which stay unchanged (see the Milestone 7 docs).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.interface import ModelUsage
from app.db.benchmarks import NotFoundError
from app.db.models import (
    BenchmarkCaseRow,
    BenchmarkScenarioRow,
    ExperimentRow,
    RunEvaluationRow,
    RunRow,
)
from app.db.runs import load_experiment_configuration
from app.evaluation.models import CaseEvaluation
from app.experiments.records import ExperimentResults, RunRecord


def save_run_evaluation(session: Session, experiment_id: str, evaluation: CaseEvaluation) -> None:
    session.add(
        RunEvaluationRow(
            run_id=evaluation.run_id,
            evaluator_version=evaluation.evaluator_version,
            experiment_id=experiment_id,
            verdict=evaluation.verdict,
            score=evaluation.score,
            evaluation=evaluation.model_dump(mode="json"),
            created_at=evaluation.created_at,
        )
    )
    session.flush()


def load_run_evaluation(
    session: Session, run_id: str, evaluator_version: str
) -> CaseEvaluation | None:
    row = session.get(RunEvaluationRow, (run_id, evaluator_version))
    return None if row is None else CaseEvaluation.model_validate(row.evaluation)


def _usage(metadata: object) -> list[ModelUsage] | None:
    if not isinstance(metadata, dict) or metadata.get("usage") is None:
        return None
    usage = metadata["usage"]
    if not isinstance(usage, list):
        return None
    return [ModelUsage.model_validate(item) for item in usage]


def load_experiment_results(session: Session, experiment_id: str) -> ExperimentResults:
    """All stored runs of an experiment with their evaluation by the experiment's evaluator.

    A run without such an evaluation is returned with ``evaluation=None`` (counted as missing by
    comparisons); experiments without a snapshot come back with ``configuration=None``.
    """
    if session.get(ExperimentRow, experiment_id) is None:
        raise NotFoundError(f"experiment '{experiment_id}' not found")
    configuration = load_experiment_configuration(session, experiment_id)
    rows = session.execute(
        select(RunRow, BenchmarkScenarioRow.slug, BenchmarkCaseRow.slug)
        .join(BenchmarkScenarioRow, BenchmarkScenarioRow.pk == RunRow.scenario_pk)
        .join(BenchmarkCaseRow, BenchmarkCaseRow.pk == BenchmarkScenarioRow.case_pk)
        .where(RunRow.experiment_id == experiment_id)
        .order_by(RunRow.scenario_pk, RunRow.repetition)
    ).all()
    evaluations: dict[str, CaseEvaluation] = {}
    if configuration is not None:
        for row in session.scalars(
            select(RunEvaluationRow).where(
                RunEvaluationRow.experiment_id == experiment_id,
                RunEvaluationRow.evaluator_version == configuration.evaluator_version,
            )
        ):
            evaluations[row.run_id] = CaseEvaluation.model_validate(row.evaluation)
    records = [
        RunRecord(
            run_id=run.id,
            case_id=case_slug,
            scenario_id=scenario_slug,
            repetition=run.repetition,
            status=run.status,
            duration_seconds=(run.finished_at - run.started_at).total_seconds()
            if run.started_at and run.finished_at
            else None,
            usage=_usage(run.agent_metadata),
            evaluation=evaluations.get(run.id),
        )
        for run, scenario_slug, case_slug in rows
    ]
    return ExperimentResults(experiment_id=experiment_id, configuration=configuration, runs=records)
