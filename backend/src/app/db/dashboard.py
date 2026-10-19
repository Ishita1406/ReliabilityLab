"""Read-only queries for the dashboard API: listings and per-run summaries.

Everything returned here is read from stored rows; nothing is synthesised. Metrics come from
``app.experiments.metrics`` over stored evaluations, so missing data stays missing.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentVersionRow,
    BenchmarkCaseRow,
    BenchmarkScenarioRow,
    BenchmarkSuiteRow,
    ExperimentRow,
    ExperimentScenarioRow,
    RunEvaluationRow,
    RunRow,
)
from app.domain.base import DomainModel
from app.domain.enums import ExperimentStatus, RunStatus
from app.evaluation.models import CaseEvaluation, FailureLabel, Verdict


class SuiteSummary(DomainModel):
    id: str
    version: str
    name: str
    description: str
    synthetic: bool
    case_count: int
    scenario_count: int
    registered_at: datetime


class ExperimentSummary(DomainModel):
    id: str
    name: str
    description: str | None
    status: ExperimentStatus
    suite_id: str
    suite_version: str
    agent_version_id: str
    agent_version_label: str
    agent_model: str
    evaluator_version: str | None
    repetitions: int
    seed: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    planned_runs: int
    stored_runs: int
    evaluated_runs: int


class RunSummary(DomainModel):
    run_id: str
    experiment_id: str
    case_id: str
    scenario_id: str
    repetition: int
    status: RunStatus
    verdict: Verdict | None
    score: float | None
    labels: list[FailureLabel]
    duration_seconds: float | None
    finished_at: datetime | None
    error_type: str | None


def list_suites(session: Session) -> list[SuiteSummary]:
    cases = (
        select(BenchmarkCaseRow.suite_pk, func.count().label("n"))
        .group_by(BenchmarkCaseRow.suite_pk)
        .subquery()
    )
    scenarios = (
        select(BenchmarkScenarioRow.suite_pk, func.count().label("n"))
        .group_by(BenchmarkScenarioRow.suite_pk)
        .subquery()
    )
    rows = session.execute(
        select(BenchmarkSuiteRow, cases.c.n, scenarios.c.n)
        .outerjoin(cases, cases.c.suite_pk == BenchmarkSuiteRow.pk)
        .outerjoin(scenarios, scenarios.c.suite_pk == BenchmarkSuiteRow.pk)
        .order_by(BenchmarkSuiteRow.slug, BenchmarkSuiteRow.version)
    ).all()
    return [
        SuiteSummary(
            id=suite.slug,
            version=suite.version,
            name=suite.name,
            description=suite.description,
            synthetic=suite.synthetic,
            case_count=case_count or 0,
            scenario_count=scenario_count or 0,
            registered_at=suite.registered_at,
        )
        for suite, case_count, scenario_count in rows
    ]


def list_experiments(session: Session, *, limit: int | None = None) -> list[ExperimentSummary]:
    """Experiments, newest first."""
    stored = (
        select(RunRow.experiment_id, func.count().label("n"))
        .group_by(RunRow.experiment_id)
        .subquery()
    )
    evaluated = (
        select(RunEvaluationRow.experiment_id, func.count().label("n"))
        .join(ExperimentRow, ExperimentRow.id == RunEvaluationRow.experiment_id)
        .where(RunEvaluationRow.evaluator_version == ExperimentRow.evaluator_version)
        .group_by(RunEvaluationRow.experiment_id)
        .subquery()
    )
    planned = (
        select(ExperimentScenarioRow.experiment_id, func.count().label("n"))
        .group_by(ExperimentScenarioRow.experiment_id)
        .subquery()
    )
    query = (
        select(
            ExperimentRow,
            BenchmarkSuiteRow.slug,
            BenchmarkSuiteRow.version,
            AgentVersionRow.version_label,
            AgentVersionRow.model_provider,
            AgentVersionRow.model_name,
            planned.c.n,
            stored.c.n,
            evaluated.c.n,
        )
        .join(BenchmarkSuiteRow, BenchmarkSuiteRow.pk == ExperimentRow.suite_pk)
        .join(AgentVersionRow, AgentVersionRow.id == ExperimentRow.agent_version_id)
        .outerjoin(planned, planned.c.experiment_id == ExperimentRow.id)
        .outerjoin(stored, stored.c.experiment_id == ExperimentRow.id)
        .outerjoin(evaluated, evaluated.c.experiment_id == ExperimentRow.id)
        .order_by(ExperimentRow.created_at.desc(), ExperimentRow.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return [
        ExperimentSummary(
            id=row.id,
            name=row.name,
            description=row.description,
            status=row.status,
            suite_id=suite_slug,
            suite_version=suite_version,
            agent_version_id=row.agent_version_id,
            agent_version_label=label,
            agent_model=f"{provider}/{model}",
            evaluator_version=row.evaluator_version,
            repetitions=row.repetitions,
            seed=row.seed,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            planned_runs=(scenarios or 0) * row.repetitions,
            stored_runs=stored_runs or 0,
            evaluated_runs=evaluated_runs or 0,
        )
        for (
            row,
            suite_slug,
            suite_version,
            label,
            provider,
            model,
            scenarios,
            stored_runs,
            evaluated_runs,
        ) in session.execute(query).all()
    ]


def run_summaries(
    session: Session,
    *,
    experiment_id: str | None = None,
    run_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[RunSummary]:
    """Runs with their evaluation by their experiment's evaluator (if any), newest first."""
    query = (
        select(
            RunRow, BenchmarkScenarioRow.slug, BenchmarkCaseRow.slug, RunEvaluationRow.evaluation
        )
        .join(BenchmarkScenarioRow, BenchmarkScenarioRow.pk == RunRow.scenario_pk)
        .join(BenchmarkCaseRow, BenchmarkCaseRow.pk == BenchmarkScenarioRow.case_pk)
        .join(ExperimentRow, ExperimentRow.id == RunRow.experiment_id)
        .outerjoin(
            RunEvaluationRow,
            (RunEvaluationRow.run_id == RunRow.id)
            & (RunEvaluationRow.evaluator_version == ExperimentRow.evaluator_version),
        )
    )
    if experiment_id is not None:
        query = query.where(RunRow.experiment_id == experiment_id).order_by(
            BenchmarkScenarioRow.pk, RunRow.repetition
        )
    else:
        query = query.order_by(RunRow.created_at.desc(), RunRow.id)
    if run_ids is not None:
        query = query.where(RunRow.id.in_(run_ids))
    if limit is not None:
        query = query.limit(limit)
    summaries = []
    for run, scenario_slug, case_slug, evaluation_json in session.execute(query).all():
        evaluation = (
            CaseEvaluation.model_validate(evaluation_json) if evaluation_json is not None else None
        )
        summaries.append(
            RunSummary(
                run_id=run.id,
                experiment_id=run.experiment_id,
                case_id=case_slug,
                scenario_id=scenario_slug,
                repetition=run.repetition,
                status=run.status,
                verdict=evaluation.verdict if evaluation else None,
                score=evaluation.score if evaluation else None,
                labels=evaluation.labels if evaluation else [],
                duration_seconds=(run.finished_at - run.started_at).total_seconds()
                if run.started_at and run.finished_at
                else None,
                finished_at=run.finished_at,
                error_type=run.error_type,
            )
        )
    return summaries
