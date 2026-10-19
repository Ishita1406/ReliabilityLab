"""Test-only adapter: AgentResult -> RunTrace.

Proves that the execution log and final answer carry everything the (future) trace collector
needs to build a valid benchmark-schema ``RunTrace``. Not production code.
"""

from typing import Any

from app.agents.interface import AgentResult
from app.domain.enums import AgentOutcome
from app.domain.trace import RunTrace
from app.runtime.execution import (
    DecisionStep,
    ExecutionLog,
    FaultInjectedStep,
    RejectedToolRequestStep,
    ToolCallStep,
)


def to_run_trace(result: AgentResult) -> RunTrace:
    return build_run_trace(
        result.run_id, result.execution, result.answer.content, result.answer.declared_outcome
    )


def build_run_trace(
    run_id: str,
    execution: ExecutionLog,
    final_content: str | None = None,
    declared_outcome: AgentOutcome | None = None,
) -> RunTrace:
    """Build a RunTrace; the final_answer event is added only when an answer exists."""
    events: list[dict[str, Any]] = []
    for step in execution.steps:
        base = {"run_id": run_id, "sequence": len(events)}
        if isinstance(step, ToolCallStep):
            events.append(
                {
                    **base,
                    "type": "tool_call",
                    "timestamp": step.tool_call.started_at,
                    "tool_call": step.tool_call,
                }
            )
        elif isinstance(step, RejectedToolRequestStep):
            events.append(
                {
                    **base,
                    "type": "error",
                    "timestamp": step.timestamp,
                    "error_type": step.error.code.value,
                    "message": step.error.message,
                }
            )
        elif isinstance(step, FaultInjectedStep):
            events.append(
                {
                    **base,
                    "type": "fault_injected",
                    "timestamp": step.timestamp,
                    "fault_id": step.fault_id,
                    "fault_type": step.fault_type,
                    "tool_call_id": step.tool_call_id,
                }
            )
        elif isinstance(step, DecisionStep):
            events.append(
                {
                    **base,
                    "type": "agent_message",
                    "timestamp": step.timestamp,
                    "content": step.message,
                }
            )
    if final_content is not None and declared_outcome is not None:
        events.append(
            {
                "run_id": run_id,
                "sequence": len(events),
                "type": "final_answer",
                "timestamp": execution.finished_at,
                "content": final_content,
                "declared_outcome": declared_outcome,
            }
        )
    return RunTrace.model_validate({"run_id": run_id, "events": events})
