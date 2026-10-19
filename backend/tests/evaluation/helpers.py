"""Build real traces for evaluation tests: a scripted agent runs through the real runner,
tools, fault injector, and trace collector, so evaluation tests exercise genuine traces."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from app.agents.interface import (
    Agent,
    AgentDescriptor,
    AgentEnvironment,
    AgentOutput,
    AgentRunMetadata,
    AgentTask,
    FinalAnswer,
    StopReason,
)
from app.domain.agent import AgentVersion
from app.domain.benchmark import BenchmarkCase, Expectation, Scenario
from app.domain.enums import AgentOutcome
from app.evaluation.engine import Evaluator
from app.evaluation.models import CaseEvaluation
from app.evaluation.samples import EVALUATION_CASE, EVALUATION_SCENARIOS
from app.runner.runner import ScenarioRunner
from app.runner.samples import deterministic_research_agent, sample_request
from app.runtime.limits import ExecutionLimits
from app.tracing.collector import TraceCollector
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy
from app.tracing.schema import CollectedTrace

SEARCH, FETCH = "search_documents", "fetch_document"
ALMANAC = "veloria-almanac"
GOOD_ANSWER = "The lighthouse was completed in 1873 [source:veloria-almanac]."


@dataclass
class ScriptedAgent:
    """Makes the listed tool calls in order, then returns ``answer`` (or raises ``crash``)."""

    calls: Sequence[tuple[str, Mapping[str, JsonValue]]] = ()
    answer: str = GOOD_ANSWER
    outcome: AgentOutcome = AgentOutcome.ANSWERED
    source_ids: list[str] = field(default_factory=list)
    crash: Exception | None = None

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(name="scripted", version="0.0.1")

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
        for name, arguments in self.calls:
            environment.tools.call(name, dict(arguments))
        if self.crash is not None:
            raise self.crash
        return AgentOutput(
            answer=FinalAnswer(
                content=self.answer, declared_outcome=self.outcome, source_ids=self.source_ids
            ),
            metadata=AgentRunMetadata(stop_reason=StopReason.COMPLETED),
        )


FETCH_ALMANAC: list[tuple[str, Mapping[str, JsonValue]]] = [
    (SEARCH, {"query": "Port Annick lighthouse completed"}),
    (FETCH, {"document_id": ALMANAC}),
]


def case_with(*criteria: dict[str, object], threshold: float = 1.0) -> BenchmarkCase:
    """The sample case with only the given criteria (as JSON-like dicts)."""
    expectation = Expectation.model_validate(
        {
            "expected_behavior": EVALUATION_CASE.expectation.expected_behavior.model_dump(),
            "evaluation_criteria": list(criteria),
            "scoring": {"pass_threshold": threshold},
        }
    )
    return EVALUATION_CASE.updated(expectation=expectation.model_dump())


def trace_for(
    agent: Agent | None = None,
    *,
    case: BenchmarkCase = EVALUATION_CASE,
    scenario: Scenario | str = "baseline",
    limits: ExecutionLimits | None = None,
    seed: int = 0,
    policy: TracePayloadPolicy = DEFAULT_POLICY,
) -> CollectedTrace:
    name = scenario if isinstance(scenario, str) else scenario.id.removeprefix(f"{case.id}.")
    resolved = EVALUATION_SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    request = sample_request(name, seed=seed).updated(
        case=case.model_dump(), scenario=resolved.model_dump()
    )
    if limits is not None:
        request = request.updated(limits=limits.model_dump())

    def factory(version: AgentVersion) -> Agent:
        return agent if agent is not None else deterministic_research_agent(version)

    return TraceCollector(policy).collect(ScenarioRunner(factory).run(request))


def evaluate(
    agent: Agent | None = None,
    *,
    case: BenchmarkCase = EVALUATION_CASE,
    scenario: Scenario | str = "baseline",
    evaluator: Evaluator | None = None,
    limits: ExecutionLimits | None = None,
    seed: int = 0,
    policy: TracePayloadPolicy = DEFAULT_POLICY,
) -> CaseEvaluation:
    resolved = EVALUATION_SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    trace = trace_for(agent, case=case, scenario=resolved, limits=limits, seed=seed, policy=policy)
    return (evaluator or Evaluator()).evaluate(trace, case, resolved)
