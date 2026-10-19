"""Execution log: the ordered record of what happened during one agent run.

The log is written by the harness (``ToolExecutor`` for tool activity, the agent only via
``note``), never assembled by the agent itself, and is kept separate from the final answer.
It carries everything the trace collector needs to build a ``RunTrace``:

* ``ToolCallStep`` wraps the benchmark schema's ``ToolCallRecord`` for every call to a
  *registered* tool, including calls rejected for invalid arguments or an exhausted budget.
* ``RejectedToolRequestStep`` records a request for a tool that does not exist. No tool ran,
  and the requested name may not even be a valid slug, so it cannot be a ``ToolCallRecord``.
* ``DecisionStep`` records a short, human-readable note from the agent about its reasoning.
* ``FaultInjectedStep`` records that the harness injected a scenario fault into a tool call.
  It is a harness event, not an agent step, so it does not count towards ``max_steps``.

Recording never fails because of limits: a call that already happened is always logged.
Limits are checked at decision points (before a note, before and after a tool call).
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.enums import FaultType
from app.domain.ids import IdPrefix, Slug, ToolCallId, derived_id
from app.domain.timestamps import UtcDateTime
from app.domain.trace import ToolCallRecord
from app.runtime.clock import Clock
from app.runtime.limits import LimitGuard
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


class FaultInjectedStep(DomainModel):
    kind: Literal["fault_injected"] = "fault_injected"
    fault_id: Slug
    fault_type: FaultType
    tool_call_id: ToolCallId
    timestamp: UtcDateTime


AnyStep = ToolCallStep | RejectedToolRequestStep | DecisionStep | FaultInjectedStep
ExecutionStep = Annotated[AnyStep, Field(discriminator="kind")]


def step_time(step: AnyStep) -> datetime:
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

    @property
    def fault_activations(self) -> list[FaultInjectedStep]:
        return [s for s in self.steps if isinstance(s, FaultInjectedStep)]


class ExecutionRecorder:
    """Collects steps for one run in the order they happen, and owns the run's limit guard."""

    def __init__(self, run_id: str, clock: Clock, guard: LimitGuard | None = None) -> None:
        self.run_id = run_id
        self.clock = clock
        self.guard = guard
        self.started_at = clock.now()
        self._steps: list[AnyStep] = []
        self._tool_call_count = 0

    @property
    def agent_step_count(self) -> int:
        """Steps attributable to the agent (fault injections are harness events)."""
        return sum(1 for s in self._steps if not isinstance(s, FaultInjectedStep))

    def next_tool_call_id(self) -> str:
        """Deterministic per-run tool-call ID, so identical runs yield identical logs."""
        self._tool_call_count += 1
        return derived_id(IdPrefix.TOOL_CALL, self.run_id, "tool-call", str(self._tool_call_count))

    def record(self, step: AnyStep) -> None:
        """Append a step. Never checks limits: what already happened is always logged."""
        self._steps.append(step)

    def note(self, message: str) -> None:
        """Record an agent decision note; this is an agent step and is limit-checked."""
        if self.guard is not None:
            self.guard.before_step(self.agent_step_count)
        self.record(DecisionStep(message=message, timestamp=self.clock.now()))

    def build(self) -> ExecutionLog:
        return ExecutionLog(
            started_at=self.started_at, finished_at=self.clock.now(), steps=list(self._steps)
        )
