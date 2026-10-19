"""Persisted trace format.

A run's trace is a list of ``TraceEventRecord`` ordered by ``sequence`` and forming a tree via
``parent_id``::

    run (sequence 0, root; spans the whole run; carries agent/model metadata)
    ├── agent_decision            note from the agent
    ├── tool_call                 request + validated arguments; spans the call
    │   ├── fault_injected        scenario fault applied to this call (reference to its config)
    │   └── tool_result           response (bounded) or structured error
    ├── tool_call (rejected)      request for a tool that does not exist; no result
    ├── final_answer              completed runs only
    └── run_error                 non-completed runs only (limit, deadline, agent exception)

Tool calls and their results are separate events linked by ``tool_call_id`` and the parent
relationship. ``CollectedTrace`` validates the tree as a whole.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, JsonValue, NonNegativeInt, StringConstraints, model_validator

from app.domain.base import DomainModel, require_unique
from app.domain.enums import FaultType
from app.domain.ids import ExperimentId, RunId, Slug, ToolCallId, TraceEventId
from app.domain.timestamps import UtcDateTime

TRACE_FORMAT_VERSION = "1"

ToolName = Annotated[str, StringConstraints(max_length=200)]
"""Not a ``Slug``: a rejected request may name a tool that does not exist (e.g. an LLM's
invented ``"Search Docs!"``), bounded to 200 characters."""


class EventKind(StrEnum):
    RUN = "run"
    AGENT_DECISION = "agent_decision"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FAULT_INJECTED = "fault_injected"
    FINAL_ANSWER = "final_answer"
    RUN_ERROR = "run_error"


class EventStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class TraceEventRecord(DomainModel):
    id: TraceEventId
    experiment_id: ExperimentId
    run_id: RunId
    parent_id: TraceEventId | None
    sequence: NonNegativeInt
    kind: EventKind
    status: EventStatus
    started_at: UtcDateTime
    ended_at: UtcDateTime

    tool_name: ToolName | None = None
    tool_call_id: ToolCallId | None = None
    arguments: JsonValue = Field(default=None, description="Sanitised tool arguments.")
    payload: JsonValue = Field(
        default=None, description="Sanitised response, message, or answer content."
    )
    payload_truncated: bool = False
    payload_original_bytes: NonNegativeInt | None = None

    error_type: str | None = None
    error_details: JsonValue = None

    agent_name: str | None = None
    agent_version: str | None = None
    model_provider: str | None = None
    model_name: str | None = None

    fault_id: Slug | None = Field(default=None, description="Scenario fault configuration ID.")
    fault_type: FaultType | None = None

    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.ended_at - self.started_at).total_seconds() * 1000

    @model_validator(mode="after")
    def _kind_rules(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be before started_at")
        is_root = self.kind is EventKind.RUN
        if is_root != (self.parent_id is None):
            raise ValueError("exactly the 'run' event has no parent")
        if is_root and self.sequence != 0:
            raise ValueError("the 'run' event must have sequence 0")
        if self.kind in {EventKind.TOOL_CALL, EventKind.TOOL_RESULT} and self.tool_name is None:
            raise ValueError(f"'{self.kind}' events require tool_name")
        if self.kind is EventKind.TOOL_RESULT and self.tool_call_id is None:
            raise ValueError("'tool_result' events require tool_call_id")
        if self.kind is EventKind.TOOL_CALL and (self.status is EventStatus.REJECTED) != (
            self.tool_call_id is None
        ):
            raise ValueError("exactly the rejected tool_call events have no tool_call_id")
        is_fault = self.kind is EventKind.FAULT_INJECTED
        if is_fault != (self.fault_id is not None and self.fault_type is not None):
            raise ValueError("exactly the fault_injected events carry fault_id and fault_type")
        if (
            self.status is not EventStatus.OK
            and self.kind is not EventKind.RUN
            and (self.error_type is None)
        ):
            raise ValueError("events with a non-ok status require error_type")
        return self


class CollectedTrace(DomainModel):
    format_version: str = TRACE_FORMAT_VERSION
    experiment_id: ExperimentId
    run_id: RunId
    events: list[TraceEventRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def _tree_integrity(self) -> Self:
        require_unique((e.id for e in self.events), "event ids")
        if [e.sequence for e in self.events] != list(range(len(self.events))):
            raise ValueError("events must be ordered by sequence 0, 1, 2, ...")
        for event in self.events:
            if (event.run_id, event.experiment_id) != (self.run_id, self.experiment_id):
                raise ValueError(f"event '{event.id}' belongs to another run or experiment")
        if self.events[0].kind is not EventKind.RUN:
            raise ValueError("the first event must be the 'run' root")

        by_id = {e.id: e for e in self.events}
        previous_start: datetime | None = None
        for event in self.events[1:]:
            parent = by_id.get(event.parent_id or "")
            if parent is None or parent.sequence >= event.sequence:
                raise ValueError(f"event '{event.id}' must have an earlier parent in this trace")
            if event.kind in {EventKind.TOOL_RESULT, EventKind.FAULT_INJECTED}:
                if parent.kind is not EventKind.TOOL_CALL or parent.tool_call_id != (
                    event.tool_call_id
                ):
                    raise ValueError(f"'{event.kind}' must be a child of its tool_call")
            elif parent.kind is not EventKind.RUN:
                raise ValueError(f"'{event.kind}' events must be children of the run root")
            if previous_start is not None and event.started_at < previous_start:
                raise ValueError("event start times must be non-decreasing by sequence")
            previous_start = event.started_at

        results = [e.tool_call_id for e in self.events if e.kind is EventKind.TOOL_RESULT]
        require_unique((r for r in results if r), "tool results per tool call")
        return self

    def children(self, event_id: str) -> list[TraceEventRecord]:
        return [e for e in self.events if e.parent_id == event_id]

    @property
    def root(self) -> TraceEventRecord:
        return self.events[0]
