"""Provider-independent agent interface.

Contract
--------
* The harness builds an ``AgentEnvironment`` (run ID, clock, tool executor, recorder) and calls
  ``run_agent(agent, task, environment)``.
* ``Agent.run`` decides what to do, calls tools **only** through ``environment.tools.call``,
  may leave short notes with ``environment.note``, and returns an ``AgentOutput``: the final
  answer plus structured metadata. It must not raise for tool failures (those arrive as
  ``ToolFailure`` results); raising means the run itself is broken (e.g. a mis-specified task).
* ``run_agent`` attaches the ``ExecutionLog`` that the harness recorded independently, so the
  execution trace is never authored by the agent and never mixed into the answer.

Nothing here assumes how decisions are made. A deterministic agent and an LLM-backed agent
implement the same ``Agent`` protocol, emit the same ``FinalAnswer`` (whose
``declared_outcome`` is the benchmark schema's ``AgentOutcome``), and use the same tools.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.benchmark import CITATION_PATTERN, ScalarValue, TaskInputs
from app.domain.enums import AgentOutcome
from app.domain.ids import RunId, Slug
from app.runtime.clock import Clock
from app.runtime.execution import ExecutionLog, ExecutionRecorder, ToolCallStep
from app.tools.base import Tool
from app.tools.executor import ToolExecutor


class AgentTask(DomainModel):
    """What the agent is asked to do. Source documents are *not* included: the agent must
    discover evidence through its tools."""

    prompt: NonEmptyStr
    available_tools: list[Slug] = Field(default_factory=list)
    variables: dict[Slug, ScalarValue] = Field(default_factory=dict)

    @classmethod
    def from_task_inputs(cls, inputs: TaskInputs) -> Self:
        return cls(
            prompt=inputs.prompt,
            available_tools=list(inputs.available_tools),
            variables=dict(inputs.variables),
        )


class FinalAnswer(DomainModel):
    content: NonEmptyStr = Field(
        description="Answer text; sources are cited inline as [source:<id>]."
    )
    declared_outcome: AgentOutcome
    source_ids: list[Slug] = Field(
        default_factory=list, description="Documents that support the answer."
    )

    @model_validator(mode="after")
    def _sources_consistent(self) -> Self:
        require_unique(self.source_ids, "source_ids")
        return self

    @property
    def cited_ids(self) -> list[str]:
        """Source IDs cited inline in ``content``, in order of first appearance."""
        seen: dict[str, None] = {}
        for match in re.finditer(CITATION_PATTERN, self.content):
            seen.setdefault(match.group(1), None)
        return list(seen)


class StopReason(StrEnum):
    COMPLETED = "completed"
    TOOL_FAILURE = "tool_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"


class EvidenceStatus(StrEnum):
    CONSISTENT = "consistent"
    CONFLICTING = "conflicting"
    INSUFFICIENT = "insufficient"
    UNAVAILABLE = "unavailable"


class EvidenceItem(DomainModel):
    source_id: Slug
    value: NonEmptyStr | None = Field(description="Extracted value, or null if none found.")
    excerpt: NonEmptyStr | None = None


class AgentRunMetadata(DomainModel):
    stop_reason: StopReason
    evidence_status: EvidenceStatus | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    unretrieved_source_ids: list[Slug] = Field(default_factory=list)


class AgentOutput(DomainModel):
    """What an agent returns. The harness adds the execution log."""

    answer: FinalAnswer
    metadata: AgentRunMetadata


class AgentDescriptor(DomainModel):
    name: Slug
    version: Slug


class AgentResult(DomainModel):
    run_id: RunId
    agent: AgentDescriptor
    answer: FinalAnswer
    metadata: AgentRunMetadata
    execution: ExecutionLog

    @model_validator(mode="after")
    def _execution_belongs_to_run(self) -> Self:
        for step in self.execution.steps:
            if isinstance(step, ToolCallStep) and step.tool_call.run_id != self.run_id:
                raise ValueError("execution log contains tool calls from another run")
        return self


@dataclass(frozen=True)
class AgentEnvironment:
    """Everything an agent may use during one run."""

    run_id: str
    clock: Clock
    tools: ToolExecutor
    recorder: ExecutionRecorder

    @classmethod
    def create(
        cls,
        run_id: str,
        tools: Sequence[Tool],
        clock: Clock,
        *,
        call_budget: int | None = None,
    ) -> Self:
        recorder = ExecutionRecorder(run_id, clock)
        return cls(
            run_id=run_id,
            clock=clock,
            tools=ToolExecutor(tools, recorder, call_budget=call_budget),
            recorder=recorder,
        )

    def note(self, message: str) -> None:
        self.recorder.note(message)


class Agent(Protocol):
    @property
    def descriptor(self) -> AgentDescriptor: ...

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput: ...


def run_agent(agent: Agent, task: AgentTask, environment: AgentEnvironment) -> AgentResult:
    """Run ``agent`` and combine its output with the independently recorded execution log."""
    output = agent.run(task, environment)
    return AgentResult(
        run_id=environment.run_id,
        agent=agent.descriptor,
        answer=output.answer,
        metadata=output.metadata,
        execution=environment.recorder.build(),
    )
