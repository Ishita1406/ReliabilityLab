"""Read-only view of a run, built from its persisted trace (``CollectedTrace``).

Evaluating the stored trace rather than in-memory runner objects means a run can be
re-evaluated later from the database, and every piece of evidence can name a trace event.
The view also records what the trace is *missing*: a truncated final answer makes absence
checks inconclusive instead of falsely passing.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import JsonValue

from app.domain.benchmark import CITATION_PATTERN
from app.domain.enums import AgentOutcome, FaultType, RunStatus
from app.tracing.schema import CollectedTrace, EventKind, EventStatus, TraceEventRecord


@dataclass(frozen=True)
class FaultView:
    event_id: str
    fault_id: str
    fault_type: FaultType


@dataclass(frozen=True)
class ToolCallView:
    event_id: str
    result_event_id: str
    sequence: int
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    status: EventStatus
    error_type: str | None
    output: JsonValue
    output_truncated: bool
    faults: tuple[FaultView, ...]

    @property
    def succeeded(self) -> bool:
        return self.status is EventStatus.OK

    @property
    def output_text(self) -> str:
        """Successful output flattened to text, for provenance searches."""
        if not self.succeeded or self.output is None:
            return ""
        return self.output if isinstance(self.output, str) else _flatten(self.output)


@dataclass(frozen=True)
class RejectedRequestView:
    event_id: str
    sequence: int
    tool_name: str
    error_type: str | None


@dataclass(frozen=True)
class FinalAnswerView:
    event_id: str
    content: str
    declared_outcome: AgentOutcome
    source_ids: tuple[str, ...]
    truncated: bool

    @property
    def cited_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for match in re.finditer(CITATION_PATTERN, self.content):
            seen.setdefault(match.group(1), None)
        return list(seen)


@dataclass(frozen=True)
class RunView:
    run_id: str
    scenario_id: str
    run_status: RunStatus
    termination_reason: str
    root_event_id: str
    started_at: datetime
    ended_at: datetime
    error_event: TraceEventRecord | None
    answer: FinalAnswerView | None
    tool_calls: tuple[ToolCallView, ...]
    rejected_requests: tuple[RejectedRequestView, ...]
    decision_count: int
    faults: tuple[tuple[FaultView, ToolCallView], ...] = field(default=())

    @property
    def step_count(self) -> int:
        """Agent steps as the runtime counts them: notes, tool calls, rejected requests."""
        return self.decision_count + len(self.tool_calls) + len(self.rejected_requests)

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    @classmethod
    def from_trace(cls, trace: CollectedTrace) -> "RunView":
        root = trace.root
        attributes = root.attributes
        calls: list[ToolCallView] = []
        rejected: list[RejectedRequestView] = []
        answer: FinalAnswerView | None = None
        error_event: TraceEventRecord | None = None
        decisions = 0
        for event in trace.events[1:]:
            if event.kind is EventKind.TOOL_CALL and event.status is EventStatus.REJECTED:
                rejected.append(
                    RejectedRequestView(
                        event_id=event.id,
                        sequence=event.sequence,
                        tool_name=event.tool_name or "",
                        error_type=event.error_type,
                    )
                )
            elif event.kind is EventKind.TOOL_CALL:
                calls.append(_tool_call(trace, event))
            elif event.kind is EventKind.AGENT_DECISION:
                decisions += 1
            elif event.kind is EventKind.FINAL_ANSWER:
                answer = _final_answer(event)
            elif event.kind is EventKind.RUN_ERROR:
                error_event = event
        return cls(
            run_id=trace.run_id,
            scenario_id=str(attributes.get("scenario_id", "")),
            run_status=RunStatus(str(attributes["run_status"])),
            termination_reason=str(attributes.get("termination_reason", "")),
            root_event_id=root.id,
            started_at=root.started_at,
            ended_at=root.ended_at,
            error_event=error_event,
            answer=answer,
            tool_calls=tuple(calls),
            rejected_requests=tuple(rejected),
            decision_count=decisions,
            faults=tuple((fault, call) for call in calls for fault in call.faults),
        )


def _tool_call(trace: CollectedTrace, call: TraceEventRecord) -> ToolCallView:
    children = trace.children(call.id)
    result = next(e for e in children if e.kind is EventKind.TOOL_RESULT)
    faults = tuple(
        FaultView(event_id=e.id, fault_id=e.fault_id or "", fault_type=e.fault_type)
        for e in children
        if e.kind is EventKind.FAULT_INJECTED and e.fault_type is not None
    )
    return ToolCallView(
        event_id=call.id,
        result_event_id=result.id,
        sequence=call.sequence,
        tool_call_id=call.tool_call_id or "",
        tool_name=call.tool_name or "",
        arguments=call.arguments if isinstance(call.arguments, dict) else {},
        status=result.status,
        error_type=result.error_type,
        output=result.payload,
        output_truncated=result.payload_truncated,
        faults=faults,
    )


def _final_answer(event: TraceEventRecord) -> FinalAnswerView:
    attributes = event.attributes
    sources = attributes.get("source_ids", [])
    return FinalAnswerView(
        event_id=event.id,
        content=event.payload if isinstance(event.payload, str) else "",
        declared_outcome=AgentOutcome(str(attributes["declared_outcome"])),
        source_ids=tuple(str(s) for s in sources) if isinstance(sources, list) else (),
        truncated=event.payload_truncated,
    )


def _flatten(value: JsonValue) -> str:
    """All string leaves (and keys' values) joined, so provenance search ignores JSON syntax."""
    parts: list[str] = []

    def visit(item: JsonValue) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif item is not None:
            parts.append(json.dumps(item))

    visit(value)
    return "\n".join(parts)
