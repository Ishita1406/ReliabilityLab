"""Reconstruct execution structure from a stored trace."""

from app.domain.enums import ToolCallStatus
from app.domain.trace import ToolCallRecord
from app.tracing.schema import CollectedTrace, EventKind, EventStatus, TraceEventRecord

_CALL_STATUS = {
    EventStatus.OK: ToolCallStatus.SUCCESS,
    EventStatus.ERROR: ToolCallStatus.ERROR,
    EventStatus.TIMEOUT: ToolCallStatus.TIMEOUT,
}


def tool_calls(trace: CollectedTrace) -> list[ToolCallRecord]:
    """Rebuild the ``ToolCallRecord`` of every executed tool call, in execution order.

    Exact for payloads that were neither redacted nor truncated; otherwise the sanitised values
    are returned. Rejected requests (no tool ran) are not tool calls and are skipped.
    """
    records: list[ToolCallRecord] = []
    for call in trace.events:
        if call.kind is not EventKind.TOOL_CALL or call.status is EventStatus.REJECTED:
            continue
        children = trace.children(call.id)
        result = next(e for e in children if e.kind is EventKind.TOOL_RESULT)
        fault = next((e for e in children if e.kind is EventKind.FAULT_INJECTED), None)
        status = _CALL_STATUS[result.status]
        records.append(
            ToolCallRecord(
                id=call.tool_call_id or "",
                run_id=call.run_id,
                tool_name=call.tool_name or "",
                arguments=call.arguments if isinstance(call.arguments, dict) else {},
                status=status,
                output=_output(result, status),
                error_message=_error_message(result, status),
                injected_fault_id=fault.fault_id if fault else None,
                started_at=call.started_at,
                finished_at=call.ended_at,
            )
        )
    return records


def _output(result: TraceEventRecord, status: ToolCallStatus) -> object:
    if status is ToolCallStatus.SUCCESS:
        return result.payload
    if status is ToolCallStatus.ERROR:
        return result.error_details
    return None


def _error_message(result: TraceEventRecord, status: ToolCallStatus) -> str | None:
    if status is ToolCallStatus.SUCCESS:
        return None
    details = result.error_details
    if isinstance(details, dict) and "message" in details:
        return f"{result.error_type}: {details['message']}"
    return str(details) if details is not None else result.error_type


def render_tree(trace: CollectedTrace) -> str:
    """Indented, human-readable outline of the event tree (for docs and debugging)."""
    lines: list[str] = []

    def visit(event: TraceEventRecord, depth: int) -> None:
        label = event.kind.value
        if event.tool_name:
            label += f" {event.tool_name}"
        if event.fault_id:
            label += f" [{event.fault_id}:{event.fault_type}]"
        suffix = f" ({event.error_type})" if event.error_type else ""
        lines.append(
            f"{'  ' * depth}#{event.sequence} {label} {event.status.value}{suffix} "
            f"{event.duration_ms:.0f}ms"
        )
        for child in trace.children(event.id):
            visit(child, depth + 1)

    visit(trace.root, 0)
    return "\n".join(lines)
