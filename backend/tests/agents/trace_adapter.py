"""Test-only adapter: AgentResult -> RunTrace.

Proves that the execution log and final answer carry everything the (future) trace collector
needs to build a valid benchmark-schema ``RunTrace``. Not production code.
"""

from typing import Any

from app.agents.interface import AgentResult
from app.domain.trace import RunTrace
from app.runtime.execution import DecisionStep, RejectedToolRequestStep, ToolCallStep


def to_run_trace(result: AgentResult) -> RunTrace:
    events: list[dict[str, Any]] = []
    for step in result.execution.steps:
        base = {"run_id": result.run_id, "sequence": len(events)}
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
        elif isinstance(step, DecisionStep):
            events.append(
                {
                    **base,
                    "type": "agent_message",
                    "timestamp": step.timestamp,
                    "content": step.message,
                }
            )
    events.append(
        {
            "run_id": result.run_id,
            "sequence": len(events),
            "type": "final_answer",
            "timestamp": result.execution.finished_at,
            "content": result.answer.content,
            "declared_outcome": result.answer.declared_outcome,
        }
    )
    return RunTrace.model_validate({"run_id": result.run_id, "events": events})
