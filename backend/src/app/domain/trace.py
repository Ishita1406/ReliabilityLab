"""Trace events and tool-call records captured during a run.

A run's trace is an ordered list of ``TraceEvent`` records (a discriminated union on ``type``).
Tool calls are recorded as ``ToolCallRecord`` entities embedded in ``tool_call`` events.
``RunTrace`` validates the integrity of a complete trace.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, NonNegativeInt, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.enums import AgentOutcome, FaultType, ToolCallStatus, TraceEventType
from app.domain.ids import IdPrefix, RunId, Slug, ToolCallId, TraceEventId, new_id
from app.domain.timestamps import UtcDateTime


class ToolCallRecord(DomainModel):
    """One invocation of a tool by the agent, including any injected fault."""

    id: ToolCallId = Field(default_factory=lambda: new_id(IdPrefix.TOOL_CALL))
    run_id: RunId
    tool_name: Slug
    arguments: dict[str, JsonValue] = Field(
        default_factory=dict, description="Arguments exactly as the agent supplied them."
    )
    status: ToolCallStatus
    output: JsonValue = Field(default=None, description="Tool output as returned to the agent.")
    error_message: NonEmptyStr | None = None
    injected_fault_id: Slug | None = Field(
        default=None, description="Fault from the scenario that altered this call, if any."
    )
    started_at: UtcDateTime
    finished_at: UtcDateTime

    @property
    def latency_ms(self) -> float:
        """Derived, not serialised: storing it would let it disagree with the timestamps."""
        return (self.finished_at - self.started_at).total_seconds() * 1000

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be before started_at")
        if self.status is ToolCallStatus.SUCCESS and self.error_message is not None:
            raise ValueError("successful tool calls must not include an error_message")
        if self.status is ToolCallStatus.ERROR and self.error_message is None:
            raise ValueError("failed tool calls must include an error_message")
        if self.status is ToolCallStatus.TIMEOUT and self.output is not None:
            raise ValueError("timed-out tool calls must not include output")
        return self


class _TraceEventBase(DomainModel):
    id: TraceEventId = Field(default_factory=lambda: new_id(IdPrefix.TRACE_EVENT))
    run_id: RunId
    sequence: NonNegativeInt = Field(description="0-based position within the run's trace.")
    timestamp: UtcDateTime


class ModelCallEvent(_TraceEventBase):
    type: Literal[TraceEventType.MODEL_CALL]
    model: NonEmptyStr
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    latency_ms: float = Field(ge=0)


class ToolCallEvent(_TraceEventBase):
    type: Literal[TraceEventType.TOOL_CALL]
    tool_call: ToolCallRecord

    @model_validator(mode="after")
    def _same_run(self) -> Self:
        if self.tool_call.run_id != self.run_id:
            raise ValueError("tool_call.run_id must match the event's run_id")
        return self


class FaultInjectedEvent(_TraceEventBase):
    type: Literal[TraceEventType.FAULT_INJECTED]
    fault_id: Slug
    fault_type: FaultType
    tool_call_id: ToolCallId


class AgentMessageEvent(_TraceEventBase):
    type: Literal[TraceEventType.AGENT_MESSAGE]
    content: NonEmptyStr


class FinalAnswerEvent(_TraceEventBase):
    type: Literal[TraceEventType.FINAL_ANSWER]
    content: str
    declared_outcome: AgentOutcome


class ErrorEvent(_TraceEventBase):
    type: Literal[TraceEventType.ERROR]
    error_type: NonEmptyStr
    message: NonEmptyStr


TraceEvent = Annotated[
    ModelCallEvent
    | ToolCallEvent
    | FaultInjectedEvent
    | AgentMessageEvent
    | FinalAnswerEvent
    | ErrorEvent,
    Field(discriminator="type"),
]


class RunTrace(DomainModel):
    """All trace events of one run, validated as a whole."""

    run_id: RunId
    events: list[TraceEvent]

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        require_unique((e.id for e in self.events), "event ids")
        foreign = sorted({e.run_id for e in self.events if e.run_id != self.run_id})
        if foreign:
            raise ValueError(f"events belong to other runs: {', '.join(foreign)}")

        sequences = [e.sequence for e in self.events]
        if sequences != list(range(len(self.events))):
            raise ValueError("event sequence numbers must be 0, 1, 2, ... in list order")

        timestamps: list[datetime] = [e.timestamp for e in self.events]
        if timestamps != sorted(timestamps):
            raise ValueError("event timestamps must be non-decreasing")

        finals = [i for i, e in enumerate(self.events) if isinstance(e, FinalAnswerEvent)]
        if len(finals) > 1:
            raise ValueError("a trace may contain at most one final_answer event")
        if finals and finals[0] != len(self.events) - 1:
            raise ValueError("final_answer must be the last event")

        tool_calls = {
            e.tool_call.id: e.tool_call for e in self.events if isinstance(e, ToolCallEvent)
        }
        for event in self.events:
            if isinstance(event, FaultInjectedEvent):
                call = tool_calls.get(event.tool_call_id)
                if call is None:
                    raise ValueError(
                        f"fault event '{event.id}' references unknown tool call "
                        f"'{event.tool_call_id}'"
                    )
                if call.injected_fault_id != event.fault_id:
                    raise ValueError(
                        f"tool call '{call.id}' does not record fault '{event.fault_id}'"
                    )
        return self

    @property
    def final_answer(self) -> FinalAnswerEvent | None:
        last = self.events[-1] if self.events else None
        return last if isinstance(last, FinalAnswerEvent) else None

    @property
    def tool_calls(self) -> list[ToolCallRecord]:
        return [e.tool_call for e in self.events if isinstance(e, ToolCallEvent)]
