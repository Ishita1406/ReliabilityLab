"""Execution log: the ordered record of what happened during one agent run.

The log is written by the harness (``ToolExecutor`` for tool activity, the agent only via
``note``), never assembled by the agent itself, and is kept separate from the final answer.
It carries everything the trace collector needs to build a ``RunTrace``:

* ``ToolCallStep`` wraps the benchmark schema's ``ToolCallRecord`` for every call to a
  *registered* tool, including calls rejected for invalid arguments or an exhausted budget.
* ``RejectedToolRequestStep`` records a request for a tool that does not exist. No tool ran,
  and the requested name may not even be a valid slug, so it cannot be a ``ToolCallRecord``.
* ``DecisionStep`` records a short, human-readable note from the agent about its reasoning.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.ids import IdPrefix, derived_id
from app.domain.timestamps import UtcDateTime
from app.domain.trace import ToolCallRecord
from app.runtime.clock import Clock
from app.tools.base import ToolError


class ToolCallStep(DomainModel):
    kind: Literal["tool_call"] = "tool_call"
    tool_call: ToolCallRecord


class RejectedToolRequestStep(DomainModel):
    kind: Literal["rejected_tool_request"] = "rejected_tool_request"
    requested_name: str
    arguments: dict[str, JsonValue]
    error: ToolError
    timestamp: UtcDateTime


class DecisionStep(DomainModel):
    kind: Literal["decision"] = "decision"
    message: NonEmptyStr
    timestamp: UtcDateTime


ExecutionStep = Annotated[
    ToolCallStep | RejectedToolRequestStep | DecisionStep, Field(discriminator="kind")
]


def step_time(step: ToolCallStep | RejectedToolRequestStep | DecisionStep) -> datetime:
    if isinstance(step, ToolCallStep):
        return step.tool_call.started_at
    return step.timestamp


class ExecutionLog(DomainModel):
    started_at: UtcDateTime
    finished_at: UtcDateTime
    steps: list[ExecutionStep]

    @model_validator(mode="after")
    def _chronological(self) -> Self:
        times = [self.started_at, *(step_time(s) for s in self.steps), self.finished_at]
        if times != sorted(times):
            raise ValueError("steps must be chronological and within started_at..finished_at")
        return self

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        return [s.tool_call for s in self.steps if isinstance(s, ToolCallStep)]


class ExecutionRecorder:
    """Collects steps for one run in the order they happen."""

    def __init__(self, run_id: str, clock: Clock) -> None:
        self.run_id = run_id
        self.clock = clock
        self.started_at = clock.now()
        self._steps: list[ToolCallStep | RejectedToolRequestStep | DecisionStep] = []
        self._tool_call_count = 0

    def next_tool_call_id(self) -> str:
        """Deterministic per-run tool-call ID, so identical runs yield identical logs."""
        self._tool_call_count += 1
        return derived_id(IdPrefix.TOOL_CALL, self.run_id, "tool-call", str(self._tool_call_count))

    def record(self, step: ToolCallStep | RejectedToolRequestStep | DecisionStep) -> None:
        self._steps.append(step)

    def note(self, message: str) -> None:
        self.record(DecisionStep(message=message, timestamp=self.clock.now()))

    def build(self) -> ExecutionLog:
        return ExecutionLog(
            started_at=self.started_at, finished_at=self.clock.now(), steps=list(self._steps)
        )
