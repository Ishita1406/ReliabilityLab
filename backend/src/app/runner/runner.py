"""Deterministic scenario runner.

``ScenarioRunner.run(request)`` executes one (agent version, benchmark case, scenario,
limits, seed) combination and returns a ``ScenarioRunResult``:

* a benchmark-schema ``Run`` with a terminal status,
* the ``EffectiveRunConfiguration`` (everything needed to reproduce the run),
* a ``Termination`` explaining why the run ended,
* the agent's ``AgentOutput`` -- only for completed runs,
* the harness-recorded ``ExecutionLog`` (always present, including for failed runs).

Every run gets a fresh clock, fresh tool instances, a fresh fault injector, and a fresh agent,
so nothing carries over between runs. Running the same request twice yields identical results.

Status mapping (a non-completed run never carries an answer):

=====================  ===================  ===============================
Termination reason     ``Run.status``       Output
=====================  ===================  ===============================
``completed``          ``completed``        agent output
``limit_exceeded``     ``limit_exceeded``   none (max steps / tool calls)
``deadline_exceeded``  ``timed_out``        none
``agent_error``        ``errored``          none (agent raised an exception)
=====================  ===================  ===============================
"""

import contextlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, NonNegativeInt, model_validator

from app.agents.interface import Agent, AgentDescriptor, AgentEnvironment, AgentOutput, AgentTask
from app.domain.agent import AgentVersion
from app.domain.base import DomainModel
from app.domain.benchmark import BenchmarkCase, Scenario
from app.domain.enums import RunStatus, ScenarioKind
from app.domain.experiment import Run, RunError
from app.domain.faults import FaultConfig
from app.domain.ids import ExperimentId, IdPrefix, RunId, Slug, derived_id
from app.domain.timestamps import UtcDateTime
from app.runner.fault_injection import FaultInjector, validate_faults
from app.runtime.clock import Clock, ManualClock
from app.runtime.execution import ExecutionLog, ToolCallStep
from app.runtime.limits import ExecutionLimitExceeded, ExecutionLimits, LimitKind, LimitViolation
from app.tools.base import Tool
from app.tools.documents import DocumentCorpus
from app.tools.fetch import MockFetchTool
from app.tools.search import MockSearchTool

RUNNER_VERSION = "0.1.0"
DEFAULT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

AgentFactory = Callable[[AgentVersion], Agent]
ToolFactory = Callable[[DocumentCorpus, Clock], Sequence[Tool]]


def default_tools(corpus: DocumentCorpus, clock: Clock) -> list[Tool]:
    return [MockSearchTool(corpus, clock), MockFetchTool(corpus, clock)]


class ScenarioConfigurationError(ValueError):
    """The request cannot be run as configured. Raised before any run starts."""


# --- request / result models ---------------------------------------------------------------


class RunRequest(DomainModel):
    experiment_id: ExperimentId
    agent_version: AgentVersion
    case: BenchmarkCase
    scenario: Scenario
    limits: ExecutionLimits
    seed: NonNegativeInt
    repetition: NonNegativeInt = 0

    @property
    def run_id(self) -> str:
        """Deterministic: the same experiment, case, scenario, and repetition -> the same ID."""
        return derived_id(
            IdPrefix.RUN, self.experiment_id, self.case.id, self.scenario.id, str(self.repetition)
        )


class FaultSeed(DomainModel):
    fault_id: Slug
    seed: int = Field(ge=0, lt=2**53, description="JSON-safe derived seed for this fault.")


class EffectiveRunConfiguration(DomainModel):
    """Everything that determined this run's behaviour, recorded for every run."""

    runner_version: str
    run_id: RunId
    experiment_id: ExperimentId
    case_id: Slug
    scenario_id: Slug
    scenario_kind: ScenarioKind
    agent: AgentDescriptor
    agent_version: AgentVersion
    available_tools: list[Slug]
    faults: list[FaultConfig]
    fault_seeds: list[FaultSeed]
    seed: NonNegativeInt
    repetition: NonNegativeInt
    limits: ExecutionLimits
    clock_start: UtcDateTime


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    LIMIT_EXCEEDED = "limit_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    AGENT_ERROR = "agent_error"


class Termination(DomainModel):
    reason: TerminationReason
    violation: LimitViolation | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.reason is TerminationReason.COMPLETED:
            if self.violation is not None or self.error is not None:
                raise ValueError("a completed run has no violation or error")
        elif self.reason is TerminationReason.AGENT_ERROR:
            if self.error is None or self.violation is not None:
                raise ValueError("agent_error requires an error and no limit violation")
        else:
            if self.violation is None:
                raise ValueError(f"'{self.reason}' requires a limit violation")
            is_deadline = self.violation.kind is LimitKind.DEADLINE
            if is_deadline != (self.reason is TerminationReason.DEADLINE_EXCEEDED):
                raise ValueError("deadline violations map to deadline_exceeded, others not")
        return self


_STATUS_FOR_REASON = {
    TerminationReason.COMPLETED: RunStatus.COMPLETED,
    TerminationReason.LIMIT_EXCEEDED: RunStatus.LIMIT_EXCEEDED,
    TerminationReason.DEADLINE_EXCEEDED: RunStatus.TIMED_OUT,
    TerminationReason.AGENT_ERROR: RunStatus.ERRORED,
}


class ScenarioRunResult(DomainModel):
    run: Run
    configuration: EffectiveRunConfiguration
    termination: Termination
    output: AgentOutput | None = Field(description="Only present for completed runs.")
    execution: ExecutionLog

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        config = self.configuration
        if (self.run.id, self.run.case_id, self.run.scenario_id) != (
            config.run_id,
            config.case_id,
            config.scenario_id,
        ):
            raise ValueError("run identity does not match the effective configuration")
        if self.run.status is not _STATUS_FOR_REASON[self.termination.reason]:
            raise ValueError(
                f"run status '{self.run.status}' contradicts termination "
                f"'{self.termination.reason}'"
            )
        completed = self.termination.reason is TerminationReason.COMPLETED
        if completed != (self.output is not None):
            raise ValueError("exactly the completed runs carry agent output")
        for step in self.execution.steps:
            if isinstance(step, ToolCallStep) and step.tool_call.run_id != self.run.id:
                raise ValueError("execution log contains tool calls from another run")
        configured = {fault.id for fault in config.faults}
        for activation in self.execution.fault_activations:
            if activation.fault_id not in configured:
                raise ValueError(f"fault '{activation.fault_id}' is not in the configuration")
        return self

    @property
    def completed(self) -> bool:
        return self.run.status is RunStatus.COMPLETED


# --- runner --------------------------------------------------------------------------------


class ScenarioRunner:
    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        tool_factory: ToolFactory = default_tools,
        clock_factory: Callable[[], Clock] = lambda: ManualClock(DEFAULT_EPOCH),
    ) -> None:
        self._agent_factory = agent_factory
        self._tool_factory = tool_factory
        self._clock_factory = clock_factory

    def run(self, request: RunRequest) -> ScenarioRunResult:
        case, scenario, version = request.case, request.scenario, request.agent_version
        if scenario.case_id != case.id:
            raise ScenarioConfigurationError(
                f"scenario '{scenario.id}' belongs to case '{scenario.case_id}', not '{case.id}'"
            )
        available = [tool for tool in case.inputs.available_tools if tool in version.tools]
        try:
            validate_faults(scenario.faults, available)
        except ValueError as exc:
            raise ScenarioConfigurationError(str(exc)) from exc

        clock = self._clock_factory()
        created_at = clock.now()
        corpus = DocumentCorpus.from_source_documents(case.inputs.documents)
        built = {tool.name: tool for tool in self._tool_factory(corpus, clock)}
        missing = [name for name in available if name not in built]
        if missing:
            raise ScenarioConfigurationError(f"no implementation for tools: {', '.join(missing)}")
        tools = [built[name] for name in available]

        injector = (
            FaultInjector(scenario.faults, seed=request.seed, scenario_id=scenario.id, clock=clock)
            if scenario.faults
            else None
        )
        run_id = request.run_id
        environment = AgentEnvironment.create(
            run_id, tools, clock, limits=request.limits, interceptor=injector
        )
        guard = environment.recorder.guard
        if guard is None:  # limits were passed, so a guard must exist
            raise RuntimeError("invariant violated: environment created without a limit guard")

        agent = self._agent_factory(version)
        configuration = EffectiveRunConfiguration(
            runner_version=RUNNER_VERSION,
            run_id=run_id,
            experiment_id=request.experiment_id,
            case_id=case.id,
            scenario_id=scenario.id,
            scenario_kind=scenario.kind,
            agent=agent.descriptor,
            agent_version=version,
            available_tools=available,
            faults=list(scenario.faults),
            fault_seeds=[
                FaultSeed(fault_id=fault_id, seed=seed)
                for fault_id, seed in FaultInjector.fault_seeds(
                    scenario.faults, seed=request.seed, scenario_id=scenario.id
                ).items()
            ],
            seed=request.seed,
            repetition=request.repetition,
            limits=request.limits,
            clock_start=created_at,
        )
        task = AgentTask(
            prompt=case.inputs.prompt,
            available_tools=available,
            variables=dict(case.inputs.variables),
        )

        started_at = clock.now()
        output: AgentOutput | None = None
        agent_error: RunError | None = None
        try:
            output = agent.run(task, environment)
        except ExecutionLimitExceeded:
            pass  # recorded on the guard and handled below
        except Exception as exc:  # noqa: BLE001 - the run boundary: any agent crash -> errored run
            agent_error = RunError(error_type=type(exc).__name__, message=str(exc) or repr(exc))

        # The agent may have swallowed the limit exception, or finished after the deadline.
        if guard.violation is None and agent_error is None:
            with contextlib.suppress(ExecutionLimitExceeded):  # recorded on the guard
                guard.check_deadline()
        execution = environment.recorder.build()

        termination = self._termination(guard.violation, agent_error)
        status = _STATUS_FOR_REASON[termination.reason]
        run = Run(
            id=run_id,
            experiment_id=request.experiment_id,
            case_id=case.id,
            scenario_id=scenario.id,
            agent_version_id=version.id,
            repetition=request.repetition,
            seed=request.seed,
            status=status,
            created_at=created_at,
            started_at=started_at,
            finished_at=execution.finished_at,
            error=self._run_error(termination),
        )
        return ScenarioRunResult(
            run=run,
            configuration=configuration,
            termination=termination,
            output=output if status is RunStatus.COMPLETED else None,
            execution=execution,
        )

    @staticmethod
    def _termination(violation: LimitViolation | None, error: RunError | None) -> Termination:
        if violation is not None:
            reason = (
                TerminationReason.DEADLINE_EXCEEDED
                if violation.kind is LimitKind.DEADLINE
                else TerminationReason.LIMIT_EXCEEDED
            )
            return Termination(reason=reason, violation=violation)
        if error is not None:
            return Termination(reason=TerminationReason.AGENT_ERROR, error=error)
        return Termination(reason=TerminationReason.COMPLETED)

    @staticmethod
    def _run_error(termination: Termination) -> RunError | None:
        if termination.error is not None:
            return termination.error
        if termination.violation is not None:
            violation = termination.violation
            return RunError(
                error_type=f"{violation.kind}_exceeded",
                message=f"{violation.kind} limit {violation.limit:g} exceeded "
                f"(observed {violation.observed:g})",
            )
        return None
