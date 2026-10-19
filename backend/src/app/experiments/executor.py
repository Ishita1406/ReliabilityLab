"""Execute an experiment's planned runs: run -> collect trace -> evaluate.

Shared by ``ExperimentService`` (which persists each run) and ``run_in_memory`` (tests and the
comparison demo). Every run's recorded configuration is checked against the experiment's
snapshot; a mismatch (e.g. the runner built a different agent implementation than the one the
experiment was created for) aborts the experiment instead of producing unattributable data.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from app.domain.benchmark import BenchmarkSuite
from app.evaluation.engine import Evaluator
from app.evaluation.models import CaseEvaluation
from app.experiments.configuration import ExperimentConfiguration, scenario_fingerprint
from app.experiments.records import ExperimentResults, RunRecord
from app.runner.fault_injection import derive_seed
from app.runner.runner import RunRequest, ScenarioRunner, ScenarioRunResult
from app.tracing.collector import TraceCollector
from app.tracing.schema import CollectedTrace


class ExperimentConfigurationError(RuntimeError):
    """The runner, evaluator, or suite does not match the experiment's configuration snapshot."""


def run_seed(experiment_seed: int, scenario_id: str, repetition: int) -> int:
    """Per-run seed, stable for a given experiment seed, scenario, and repetition."""
    return derive_seed(experiment_seed, scenario_id, str(repetition))


@dataclass(frozen=True)
class ExecutedRun:
    result: ScenarioRunResult
    trace: CollectedTrace
    evaluation: CaseEvaluation

    def record(self) -> RunRecord:
        run, output = self.result.run, self.result.output
        duration = (
            (run.finished_at - run.started_at).total_seconds()
            if run.started_at and run.finished_at
            else None
        )
        return RunRecord(
            run_id=run.id,
            case_id=run.case_id,
            scenario_id=run.scenario_id,
            repetition=run.repetition,
            status=run.status,
            duration_seconds=duration,
            usage=output.metadata.usage if output is not None else None,
            evaluation=self.evaluation,
        )


def check_inputs(
    configuration: ExperimentConfiguration, suite: BenchmarkSuite, evaluator: Evaluator
) -> None:
    """The suite and evaluator must be exactly the ones the snapshot recorded."""
    if (suite.id, suite.version) != (configuration.suite_id, configuration.suite_version):
        raise ExperimentConfigurationError(
            f"suite {suite.id}@{suite.version} is not the experiment's "
            f"{configuration.suite_id}@{configuration.suite_version}"
        )
    for scenario_id, fingerprint in configuration.scenario_fingerprints.items():
        if scenario_fingerprint(suite, scenario_id) != fingerprint:
            raise ExperimentConfigurationError(f"scenario '{scenario_id}' changed since creation")
    if evaluator.version != configuration.evaluator_version:
        raise ExperimentConfigurationError(
            f"evaluator '{evaluator.version}' is not the experiment's "
            f"'{configuration.evaluator_version}'"
        )


def planned_requests(
    experiment_id: str, configuration: ExperimentConfiguration, suite: BenchmarkSuite
) -> Iterator[RunRequest]:
    for scenario_id in configuration.scenario_ids:
        scenario = suite.get_scenario(scenario_id)
        case = suite.get_case(scenario.case_id)
        for repetition in range(configuration.repetitions):
            yield RunRequest(
                experiment_id=experiment_id,
                agent_version=configuration.agent_version,
                case=case,
                scenario=scenario,
                limits=configuration.limits,
                seed=run_seed(configuration.seed, scenario_id, repetition),
                repetition=repetition,
            )


def execute(
    request: RunRequest,
    configuration: ExperimentConfiguration,
    runner: ScenarioRunner,
    evaluator: Evaluator,
    collector: TraceCollector,
) -> ExecutedRun:
    result = runner.run(request)
    problems = configuration.run_mismatches(result.configuration)
    if result.configuration.faults != request.scenario.faults:
        problems.append("faults")
    if problems:
        raise ExperimentConfigurationError(
            f"run '{result.run.id}' does not match the experiment snapshot: {', '.join(problems)}"
        )
    trace = collector.collect(result)
    evaluation = evaluator.evaluate(trace, request.case, request.scenario)
    return ExecutedRun(result=result, trace=trace, evaluation=evaluation)


def run_in_memory(
    experiment_id: str,
    configuration: ExperimentConfiguration,
    suite: BenchmarkSuite,
    runner: ScenarioRunner,
    evaluator: Evaluator,
    collector: TraceCollector | None = None,
) -> ExperimentResults:
    check_inputs(configuration, suite, evaluator)
    collector = collector or TraceCollector()
    records = [
        execute(request, configuration, runner, evaluator, collector).record()
        for request in planned_requests(experiment_id, configuration, suite)
    ]
    return ExperimentResults(experiment_id=experiment_id, configuration=configuration, runs=records)
