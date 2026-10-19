"""Create experiments and execute them through the scenario runner with persistence.

Transaction boundaries:

* registering content and creating an experiment: one transaction each;
* every run: ``runner.run`` (in memory) -> ``TraceCollector.collect`` -> ``Evaluator.evaluate``
  -> one transaction that writes the run row, all of its trace events, and its evaluation. A
  run is stored with its trace and evaluation or not at all;
* experiment status transitions (pending -> running -> completed / failed): their own short
  transactions, so a failure while running leaves the experiment visibly ``failed`` with the
  runs that were committed before it.
"""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.agents.interface import AgentDescriptor
from app.db.benchmarks import NotFoundError, load_agent_version, load_suite, save_agent, save_suite
from app.db.evaluations import save_run_evaluation
from app.db.runs import (
    create_experiment,
    load_experiment,
    load_experiment_configuration,
    save_run,
    update_experiment_state,
)
from app.domain.agent import AgentConfig, AgentVersion
from app.domain.benchmark import BenchmarkSuite
from app.domain.enums import ExperimentStatus
from app.domain.experiment import Experiment
from app.domain.timestamps import utc_now
from app.evaluation.engine import Evaluator
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.executor import (
    ExperimentConfigurationError,
    check_inputs,
    execute,
    planned_requests,
)
from app.runner.runner import ScenarioRunner
from app.runtime.limits import ExecutionLimits
from app.tracing.collector import TraceCollector
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy


class ExperimentStateError(RuntimeError):
    """The experiment is not in a state that allows the requested operation."""


class ExperimentService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        policy: TracePayloadPolicy = DEFAULT_POLICY,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._policy = policy
        self._collector = TraceCollector(policy)
        self._now = now

    def register(self, suite: BenchmarkSuite, config: AgentConfig, version: AgentVersion) -> None:
        """Idempotently store a suite and an agent version (conflicting content is refused)."""
        with self._sessions.begin() as session:
            save_suite(session, suite)
            save_agent(session, config, version)

    def create_experiment(
        self,
        *,
        name: str,
        suite_id: str,
        suite_version: str,
        scenario_ids: list[str],
        agent_version_id: str,
        repetitions: int,
        seed: int,
        limits: ExecutionLimits,
        evaluator_version: str,
        agent_implementation: AgentDescriptor,
        description: str | None = None,
    ) -> Experiment:
        """Create a pending experiment and record its immutable configuration snapshot."""
        experiment = Experiment(
            name=name,
            description=description,
            suite_id=suite_id,
            suite_version=suite_version,
            agent_version_id=agent_version_id,
            scenario_ids=scenario_ids,
            repetitions=repetitions,
            seed=seed,
            created_at=self._now(),
        )
        with self._sessions.begin() as session:
            suite = load_suite(session, suite_id, suite_version)
            for scenario_id in scenario_ids:
                if scenario_id not in {s.id for s in suite.scenarios}:
                    raise NotFoundError(f"scenario '{scenario_id}' not found in {suite_id}")
            configuration = ExperimentConfiguration.build(
                suite=suite,
                scenario_ids=scenario_ids,
                agent_version=load_agent_version(session, agent_version_id),
                agent_implementation=agent_implementation,
                limits=limits,
                repetitions=repetitions,
                seed=seed,
                evaluator_version=evaluator_version,
            )
            create_experiment(session, experiment, configuration)
        return experiment

    def run_experiment(
        self, experiment_id: str, runner: ScenarioRunner, evaluator: Evaluator
    ) -> list[str]:
        """Execute and evaluate every (scenario, repetition) of a pending experiment.

        The runner's agent, the evaluator, and the suite must match the configuration snapshot;
        otherwise ``ExperimentConfigurationError`` is raised (before any run when detectable).
        """
        with self._sessions.begin() as session:
            experiment = load_experiment(session, experiment_id)
            if experiment.status is not ExperimentStatus.PENDING:
                raise ExperimentStateError(
                    f"experiment '{experiment_id}' is '{experiment.status}', not pending"
                )
            configuration = load_experiment_configuration(session, experiment_id)
            if configuration is None:
                raise ExperimentConfigurationError(
                    f"experiment '{experiment_id}' has no configuration snapshot"
                )
            suite = load_suite(session, experiment.suite_id, experiment.suite_version)
            stored_version = load_agent_version(session, experiment.agent_version_id)
            if stored_version != configuration.agent_version:
                raise ExperimentConfigurationError("agent version changed since creation")
            check_inputs(configuration, suite, evaluator)
            experiment = experiment.updated(status=ExperimentStatus.RUNNING, started_at=self._now())
            update_experiment_state(session, experiment)

        run_ids: list[str] = []
        try:
            for request in planned_requests(experiment.id, configuration, suite):
                executed = execute(request, configuration, runner, evaluator, self._collector)
                with self._sessions.begin() as session:
                    save_run(session, executed.result, executed.trace, self._policy)
                    save_run_evaluation(session, experiment.id, executed.evaluation)
                run_ids.append(executed.result.run.id)
        except Exception:
            # Record the failure, then let the original error propagate unchanged.
            self._finish(experiment, ExperimentStatus.FAILED)
            raise
        self._finish(experiment, ExperimentStatus.COMPLETED)
        return run_ids

    def _finish(self, experiment: Experiment, status: ExperimentStatus) -> None:
        finished = experiment.updated(status=status, finished_at=self._now())
        with self._sessions.begin() as session:
            update_experiment_state(session, finished)
