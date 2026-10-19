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

import sys
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.agents.interface import AgentDescriptor
from app.db.benchmarks import NotFoundError, load_agent_version, load_suite, save_agent, save_suite
from app.db.jobs import ensure_job, mark_queued
from app.db.runs import create_experiment, load_experiment, load_experiment_configuration
from app.domain.agent import AgentConfig, AgentVersion
from app.domain.benchmark import BenchmarkSuite
from app.domain.enums import ExperimentStatus
from app.domain.experiment import Experiment
from app.domain.timestamps import utc_now
from app.evaluation.engine import Evaluator
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.executor import ExperimentConfigurationError, check_inputs
from app.jobs.executor import JobExecutor, JobOutcome, JobSettings
from app.runner.runner import ScenarioRunner
from app.runtime.limits import ExecutionLimits
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy

INLINE_WORKER = "inline"
INLINE_LEASE_SECONDS = 24 * 3600.0
"""Synchronous execution holds the lease for the whole run; nothing else can take it over."""


class ExperimentStateError(RuntimeError):
    """The experiment is not in a state that allows the requested operation."""


class ExperimentService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        policy: TracePayloadPolicy = DEFAULT_POLICY,
        now: Callable[[], datetime] = utc_now,
        max_attempts: int = 3,
    ) -> None:
        self._sessions = sessions
        self._policy = policy
        self._now = now
        self._max_attempts = max_attempts

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
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
    ) -> Experiment:
        """Create a pending experiment, its immutable configuration snapshot, and its job row
        (with the creation idempotency key, if any) in one transaction."""
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
            ensure_job(
                session,
                experiment.id,
                now=experiment.created_at,
                max_attempts=self._max_attempts,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
        return experiment

    def run_experiment(
        self, experiment_id: str, runner: ScenarioRunner, evaluator: Evaluator
    ) -> list[str]:
        """Execute a pending experiment synchronously, in this process.

        Same code path as background execution (``app.jobs.executor.JobExecutor``) with a single
        attempt and no concurrency limit. The runner's agent, the evaluator, and the suite must
        match the snapshot: detectable mismatches raise ``ExperimentConfigurationError`` before
        anything starts (the experiment stays ``pending``); errors during execution mark it
        ``failed`` and are re-raised. Returns the stored run IDs in planned order."""
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
            ensure_job(session, experiment_id, now=self._now(), max_attempts=1)
            mark_queued(session, experiment_id, self._now())

        executor = JobExecutor(
            self._sessions,
            settings=JobSettings(lease_seconds=INLINE_LEASE_SECONDS, max_running=sys.maxsize),
            now=self._now,
            policy=self._policy,
        )
        result = executor.execute(experiment_id, INLINE_WORKER, runner=runner, evaluator=evaluator)
        if result.error is not None:
            raise result.error
        if result.outcome is not JobOutcome.COMPLETED:
            raise ExperimentStateError(f"experiment '{experiment_id}' ended as {result.outcome}")
        return result.run_ids
