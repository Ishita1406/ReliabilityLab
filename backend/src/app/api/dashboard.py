"""Read API for the dashboard: overview, benchmarks, experiments, comparisons, runs.

All responses come from persisted data. Unavailable values are explicit (``null`` or a
``MetricValue`` with ``unavailable_reason``), never zero-filled.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ApiModel, Sessions
from app.db.benchmarks import NotFoundError, load_suite
from app.db.dashboard import (
    ExperimentSummary,
    RunSummary,
    SuiteSummary,
    list_experiments,
    list_suites,
    run_summaries,
)
from app.db.evaluations import load_experiment_results, load_run_evaluation
from app.db.runs import load_experiment_configuration, load_run, load_trace
from app.domain.agent import AgentVersion
from app.domain.benchmark import BenchmarkSuite
from app.domain.enums import AgentOutcome, ExperimentStatus
from app.evaluation.diagnosis import Diagnosis, diagnose
from app.evaluation.models import CaseEvaluation, FailureLabel, Verdict
from app.experiments.comparison import ExperimentComparison, compare
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.metrics import ExperimentMetrics, compute_metrics
from app.experiments.policy import PolicyEvaluation, RegressionPolicy, evaluate_policy
from app.runner.runner import TerminationReason
from app.tracing.schema import CollectedTrace

router = APIRouter(prefix="/api", tags=["dashboard"])


# --- benchmarks ---------------------------------------------------------------------------------


@router.get("/suites")
def get_suites(sessions: Sessions) -> list[SuiteSummary]:
    with sessions() as session:
        return list_suites(session)


@router.get("/suites/{suite_id}/{version}")
def get_suite(suite_id: str, version: str, sessions: Sessions) -> BenchmarkSuite:
    with sessions() as session:
        return load_suite(session, suite_id, version)


# --- experiments --------------------------------------------------------------------------------


class ExperimentDetail(ApiModel):
    experiment: ExperimentSummary
    configuration: ExperimentConfiguration | None
    metrics: ExperimentMetrics
    runs: list[RunSummary]


def _summary(sessions: Sessions, experiment_id: str) -> ExperimentSummary:
    with sessions() as session:
        for summary in list_experiments(session):
            if summary.id == experiment_id:
                return summary
    raise NotFoundError(f"experiment '{experiment_id}' not found")


@router.get("/experiments")
def get_experiments(sessions: Sessions) -> list[ExperimentSummary]:
    with sessions() as session:
        return list_experiments(session)


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, sessions: Sessions) -> ExperimentDetail:
    summary = _summary(sessions, experiment_id)
    with sessions() as session:
        results = load_experiment_results(session, experiment_id)
        runs = run_summaries(session, experiment_id=experiment_id)
    return ExperimentDetail(
        experiment=summary,
        configuration=results.configuration,
        metrics=compute_metrics(results),
        runs=runs,
    )


# --- overview -----------------------------------------------------------------------------------


class ExperimentHeadline(ApiModel):
    experiment: ExperimentSummary
    metrics: ExperimentMetrics


class Overview(ApiModel):
    experiment_counts: dict[ExperimentStatus, int]
    total_experiments: int
    latest_completed: ExperimentHeadline | None
    recent_experiments: list[ExperimentHeadline]
    recent_runs: list[RunSummary]


@router.get("/overview")
def get_overview(sessions: Sessions) -> Overview:
    """Counts, the latest completed experiment's metrics, and recent activity.

    Metrics are per experiment: runs of different configurations are never pooled."""
    with sessions() as session:
        experiments = list_experiments(session)
        recent = []
        for summary in experiments[:8]:
            results = load_experiment_results(session, summary.id)
            recent.append(ExperimentHeadline(experiment=summary, metrics=compute_metrics(results)))
        recent_runs = run_summaries(session, limit=15)
    counts = dict.fromkeys(ExperimentStatus, 0)
    for summary in experiments:
        counts[summary.status] += 1
    completed = [h for h in recent if h.experiment.status is ExperimentStatus.COMPLETED]
    return Overview(
        experiment_counts=counts,
        total_experiments=len(experiments),
        latest_completed=completed[0] if completed else None,
        recent_experiments=recent,
        recent_runs=recent_runs,
    )


# --- comparisons --------------------------------------------------------------------------------


class ComparisonResponse(ApiModel):
    comparison: ExperimentComparison
    policy: RegressionPolicy
    evaluation: PolicyEvaluation


@router.get("/comparisons")
def get_comparison(
    baseline: Annotated[str, Query(min_length=1)],
    candidate: Annotated[str, Query(min_length=1)],
    sessions: Sessions,
) -> ComparisonResponse:
    """Compare two stored experiments under the default regression policy."""
    with sessions() as session:
        base = load_experiment_results(session, baseline)
        cand = load_experiment_results(session, candidate)
    comparison = compare(base, cand)
    policy = RegressionPolicy()
    return ComparisonResponse(
        comparison=comparison, policy=policy, evaluation=evaluate_policy(comparison, policy)
    )


# --- runs ---------------------------------------------------------------------------------------


class RunDetail(ApiModel):
    run: RunSummary
    termination_reason: TerminationReason
    error_message: str | None
    declared_outcome: AgentOutcome | None
    final_answer: str | None
    agent_version: AgentVersion | None
    evaluator_version: str | None
    evaluation: CaseEvaluation | None
    trace: CollectedTrace | None
    trace_unavailable_reason: str | None
    diagnosis: Diagnosis | None
    diagnosis_unavailable_reason: str | None


@router.get("/runs/{run_id}")
def get_run(run_id: str, sessions: Sessions) -> RunDetail:
    with sessions() as session:
        stored = load_run(session, run_id)
        (summary,) = run_summaries(session, run_ids=[run_id])
        configuration = load_experiment_configuration(session, stored.run.experiment_id)
        evaluator_version = configuration.evaluator_version if configuration else None
        evaluation = (
            load_run_evaluation(session, run_id, evaluator_version) if evaluator_version else None
        )
        trace: CollectedTrace | None = None
        trace_reason: str | None = None
        if stored.trace_purged_at is not None:
            trace_reason = f"trace purged at {stored.trace_purged_at.isoformat()}"
        else:
            trace = load_trace(session, run_id)
    diagnosis: Diagnosis | None = None
    if evaluation is None:
        diagnosis_reason: str | None = "run has no stored evaluation"
    elif trace is None:
        diagnosis_reason = "trace unavailable"
    else:
        diagnosis, diagnosis_reason = diagnose(evaluation, trace), None
    agent_version = stored.effective_configuration.get("agent_version")
    return RunDetail(
        run=summary,
        termination_reason=stored.termination_reason,
        error_message=stored.run.error.message if stored.run.error else None,
        declared_outcome=stored.declared_outcome,
        final_answer=stored.final_answer,
        agent_version=AgentVersion.model_validate(agent_version) if agent_version else None,
        evaluator_version=evaluator_version,
        evaluation=evaluation,
        trace=trace,
        trace_unavailable_reason=trace_reason,
        diagnosis=diagnosis,
        diagnosis_unavailable_reason=diagnosis_reason,
    )


@router.get("/experiments/{experiment_id}/runs")
def get_experiment_runs(
    experiment_id: str,
    sessions: Sessions,
    verdict: Verdict | None = None,
    label: FailureLabel | None = None,
) -> list[RunSummary]:
    _summary(sessions, experiment_id)  # 404 for unknown experiments
    with sessions() as session:
        runs = run_summaries(session, experiment_id=experiment_id)
    return [
        r
        for r in runs
        if (verdict is None or r.verdict is verdict) and (label is None or label in r.labels)
    ]
