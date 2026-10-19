"""Create experiments and execute them through the scenario runner with persistence.

Transaction boundaries:

* registering content and creating an experiment: one transaction each;
* every run: ``runner.run`` (in memory) -> ``TraceCollector.collect`` -> one transaction that
  writes the run row and all of its trace events. A run is stored with its trace or not at all;
* experiment status transitions (pending -> running -> completed / failed): their own short
  transactions, so a failure while running leaves the experiment visibly ``failed`` with the
  runs that were committed before it.
"""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db.benchmarks import load_agent_version, load_suite, save_agent, save_suite
from app.db.runs import create_experiment, load_experiment, save_run, update_experiment_state
from app.domain.agent import AgentConfig, AgentVersion
from app.domain.benchmark import BenchmarkSuite
from app.domain.enums import ExperimentStatus
from app.domain.experiment import Experiment
from app.domain.timestamps import utc_now
from app.runner.fault_injection import derive_seed
from app.runner.runner import RunRequest, ScenarioRunner
from app.runtime.limits import ExecutionLimits
from app.tracing.collector import TraceCollector
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy


class ExperimentStateError(RuntimeError):
    """The experiment is not in a state that allows the requested operation."""


def run_seed(experiment_seed: int, scenario_id: str, repetition: int) -> int:
    """Per-run seed, stable for a given experiment seed, scenario, and repetition."""
    return derive_seed(experiment_seed, scenario_id, str(repetition))


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
        description: str | None = None,
    ) -> Experiment:
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
            create_experiment(session, experiment)
        return experiment

    def run_experiment(
        self, experiment_id: str, runner: ScenarioRunner, limits: ExecutionLimits
    ) -> list[str]:
        """Execute every (scenario, repetition) of a pending experiment; return the run IDs."""
        with self._sessions.begin() as session:
            experiment = load_experiment(session, experiment_id)
            if experiment.status is not ExperimentStatus.PENDING:
                raise ExperimentStateError(
                    f"experiment '{experiment_id}' is '{experiment.status}', not pending"
                )
            suite = load_suite(session, experiment.suite_id, experiment.suite_version)
            agent_version = load_agent_version(session, experiment.agent_version_id)
            experiment = experiment.updated(status=ExperimentStatus.RUNNING, started_at=self._now())
            update_experiment_state(session, experiment)

        run_ids: list[str] = []
        try:
            for scenario_id in experiment.scenario_ids:
                scenario = suite.get_scenario(scenario_id)
                case = suite.get_case(scenario.case_id)
                for repetition in range(experiment.repetitions):
                    request = RunRequest(
                        experiment_id=experiment.id,
                        agent_version=agent_version,
                        case=case,
                        scenario=scenario,
                        limits=limits,
                        seed=run_seed(experiment.seed, scenario_id, repetition),
                        repetition=repetition,
                    )
                    result = runner.run(request)
                    trace = self._collector.collect(result)
                    with self._sessions.begin() as session:
                        save_run(session, result, trace, self._policy)
                    run_ids.append(result.run.id)
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
