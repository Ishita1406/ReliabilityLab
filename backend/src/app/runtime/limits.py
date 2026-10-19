"""Hard execution limits for a run: step count, tool-call count, and a deadline.

``LimitGuard`` is consulted by the ``ExecutionRecorder`` (every agent step) and the
``ToolExecutor`` (before and after every tool call). When a limit would be exceeded it records
a ``LimitViolation`` and raises ``ExecutionLimitExceeded``.

``ExecutionLimitExceeded`` deliberately derives from ``BaseException`` (like
``KeyboardInterrupt``), so an agent's ``except Exception`` cannot swallow it. Once tripped, the
guard stays tripped: every later check raises again, and the runner inspects ``violation``
after the agent returns, so even an agent that catches ``BaseException`` cannot turn a
limit violation into a successful run.

Enforcement is cooperative: limits are checked whenever the agent records a step or calls a
tool. An agent that loops without doing either cannot be interrupted (see the M4 docs).
"""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, PositiveFloat, PositiveInt

from app.domain.agent import AgentParameters
from app.domain.base import DomainModel
from app.domain.timestamps import UtcDateTime
from app.runtime.clock import Clock


class ExecutionLimits(DomainModel):
    max_steps: PositiveInt = Field(description="Max recorded agent steps (notes + tool calls).")
    max_tool_calls: PositiveInt = Field(description="Max tool call attempts, any tool.")
    deadline_seconds: PositiveFloat = Field(description="Wall-clock budget on the run's clock.")

    @classmethod
    def from_agent_parameters(cls, parameters: AgentParameters, *, max_tool_calls: int) -> Self:
        return cls(
            max_steps=parameters.max_steps,
            max_tool_calls=max_tool_calls,
            deadline_seconds=parameters.timeout_seconds,
        )


class LimitKind(StrEnum):
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    DEADLINE = "deadline"


class LimitViolation(DomainModel):
    kind: LimitKind
    limit: float = Field(description="Configured limit (count, or seconds for the deadline).")
    observed: float = Field(description="Value that exceeded it.")
    detected_at: UtcDateTime


class ExecutionLimitExceeded(BaseException):
    """Aborts the agent. Not an ``Exception`` so generic handlers cannot swallow it."""

    def __init__(self, violation: LimitViolation) -> None:
        super().__init__(f"{violation.kind} limit {violation.limit:g} exceeded")
        self.violation = violation


class LimitGuard:
    def __init__(self, limits: ExecutionLimits, clock: Clock, started_at: datetime) -> None:
        self.limits = limits
        self._clock = clock
        self.started_at = started_at
        self.deadline = started_at + timedelta(seconds=limits.deadline_seconds)
        self.violation: LimitViolation | None = None

    def before_step(self, steps_so_far: int) -> None:
        """Call before recording another agent step."""
        self._raise_if_tripped()
        self.check_deadline()
        if steps_so_far + 1 > self.limits.max_steps:
            self._trip(LimitKind.MAX_STEPS, self.limits.max_steps, steps_so_far + 1)

    def before_tool_call(self, calls_so_far: int, steps_so_far: int) -> None:
        """Call before attempting another tool call (which will also be a step)."""
        self._raise_if_tripped()
        self.check_deadline()
        if calls_so_far + 1 > self.limits.max_tool_calls:
            self._trip(LimitKind.MAX_TOOL_CALLS, self.limits.max_tool_calls, calls_so_far + 1)
        if steps_so_far + 1 > self.limits.max_steps:
            self._trip(LimitKind.MAX_STEPS, self.limits.max_steps, steps_so_far + 1)

    def check_deadline(self) -> None:
        self._raise_if_tripped()
        now = self._clock.now()
        if now > self.deadline:
            elapsed = (now - self.started_at).total_seconds()
            self._trip(LimitKind.DEADLINE, self.limits.deadline_seconds, elapsed)

    def _raise_if_tripped(self) -> None:
        if self.violation is not None:
            raise ExecutionLimitExceeded(self.violation)

    def _trip(self, kind: LimitKind, limit: float, observed: float) -> None:
        self.violation = LimitViolation(
            kind=kind, limit=limit, observed=observed, detected_at=self._clock.now()
        )
        raise ExecutionLimitExceeded(self.violation)
