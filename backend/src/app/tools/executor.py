"""Executes tool calls on behalf of an agent.

The executor is the single boundary between an agent's decisions and tool side effects:

0. check hard execution limits (``LimitGuard``; raises ``ExecutionLimitExceeded``),
1. resolve the tool by name (unknown names are rejected and logged, not raised),
2. enforce the soft per-run call budget (returned to the agent as ``budget_exhausted``),
3. validate raw JSON arguments against the tool's input model,
4. invoke the tool, optionally through a ``CallInterceptor`` (fault injection), and translate
   ``ToolExecutionError`` / ``ToolTimeoutError``,
5. validate the raw output against the tool's output model (``malformed_response`` if not),
6. record a ``ToolCallRecord`` for every call to a registered tool (plus a
   ``FaultInjectedStep`` when an interceptor injected a fault), then re-check the deadline.

Agents receive a ``ToolResult`` and never see exceptions for expected tool failures.
Unexpected exceptions from a tool are bugs and propagate unchanged.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, JsonValue, ValidationError

from app.domain.enums import FaultType, ToolCallStatus
from app.domain.trace import ToolCallRecord
from app.runtime.execution import (
    ExecutionRecorder,
    FaultInjectedStep,
    RejectedToolRequestStep,
    ToolCallStep,
)
from app.tools.base import (
    FieldError,
    Tool,
    ToolError,
    ToolErrorCode,
    ToolExecutionError,
    ToolFailure,
    ToolResult,
    ToolSuccess,
    ToolTimeoutError,
)


def _field_errors(exc: ValidationError) -> list[FieldError]:
    return [
        FieldError(
            location=".".join(str(part) for part in err["loc"]) or "arguments", message=err["msg"]
        )
        for err in exc.errors()
    ]


@dataclass(frozen=True)
class InjectedFault:
    fault_id: str
    fault_type: FaultType


@dataclass(frozen=True)
class Interception:
    """What an interceptor decided for one call: a raw payload or an error, plus the fault
    that produced it (``None`` when the call passed through untouched)."""

    raw: JsonValue = None
    error: ToolError | None = None
    fault: InjectedFault | None = None


class CallInterceptor(Protocol):
    def intercept(
        self, tool: Tool, arguments: BaseModel, invoke: Callable[[], JsonValue]
    ) -> Interception:
        """Decide how one validated call proceeds. ``invoke`` runs the real tool; it may raise
        ``ToolExecutionError`` / ``ToolTimeoutError``, which must be allowed to propagate."""
        ...


class ToolExecutor:
    def __init__(
        self,
        tools: Sequence[Tool],
        recorder: ExecutionRecorder,
        *,
        call_budget: int | None = None,
        interceptor: CallInterceptor | None = None,
    ) -> None:
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate tool names: {names}")
        if call_budget is not None and call_budget < 1:
            raise ValueError("call_budget must be positive")
        self._tools = {tool.name: tool for tool in tools}
        self._recorder = recorder
        self._call_budget = call_budget
        self._interceptor = interceptor
        self._calls_made = 0

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    @property
    def calls_made(self) -> int:
        return self._calls_made

    def call(self, tool_name: str, arguments: Mapping[str, JsonValue]) -> ToolResult:
        """Execute one tool call and record it. Never raises for expected tool failures."""
        raw_arguments = dict(arguments)
        guard = self._recorder.guard
        if guard is not None:
            guard.before_tool_call(self._calls_made, self._recorder.agent_step_count)
        clock = self._recorder.clock
        started_at = clock.now()
        self._calls_made += 1

        tool = self._tools.get(tool_name)
        if tool is None:
            error = ToolError.create(
                ToolErrorCode.UNKNOWN_TOOL,
                f"unknown tool '{tool_name}'; available: {', '.join(self.tool_names) or 'none'}",
            )
            self._recorder.record(
                RejectedToolRequestStep(
                    requested_name=tool_name,
                    arguments=raw_arguments,
                    error=error,
                    timestamp=started_at,
                )
            )
            return ToolFailure(call_id=None, error=error)

        call_id = self._recorder.next_tool_call_id()
        outcome, fault = self._execute(tool, raw_arguments)
        finished_at = clock.now()
        fault_id = fault.fault_id if fault is not None else None

        # ToolError is itself a BaseModel, so the error case must be checked first.
        if isinstance(outcome, ToolError):
            timed_out = outcome.code is ToolErrorCode.TIMEOUT
            record = ToolCallRecord(
                id=call_id,
                run_id=self._recorder.run_id,
                tool_name=tool.name,
                arguments=raw_arguments,
                status=ToolCallStatus.TIMEOUT if timed_out else ToolCallStatus.ERROR,
                # What the agent received; the trace schema forbids output on timeouts.
                output=None if timed_out else outcome.model_dump(mode="json"),
                error_message=f"{outcome.code}: {outcome.message}",
                injected_fault_id=fault_id,
                started_at=started_at,
                finished_at=finished_at,
            )
            result: ToolResult = ToolFailure(call_id=call_id, error=outcome)
        else:
            record = ToolCallRecord(
                id=call_id,
                run_id=self._recorder.run_id,
                tool_name=tool.name,
                arguments=raw_arguments,
                status=ToolCallStatus.SUCCESS,
                output=outcome.model_dump(mode="json"),
                injected_fault_id=fault_id,
                started_at=started_at,
                finished_at=finished_at,
            )
            result = ToolSuccess(call_id=call_id, output=outcome)

        self._recorder.record(ToolCallStep(tool_call=record))
        if fault is not None:
            self._recorder.record(
                FaultInjectedStep(
                    fault_id=fault.fault_id,
                    fault_type=fault.fault_type,
                    tool_call_id=call_id,
                    timestamp=started_at,
                )
            )
        if guard is not None:
            guard.check_deadline()  # the call itself may have run past the deadline
        return result

    def _execute(
        self, tool: Tool, arguments: dict[str, JsonValue]
    ) -> tuple[BaseModel | ToolError, InjectedFault | None]:
        if self._call_budget is not None and self._calls_made > self._call_budget:
            budget_error = ToolError.create(
                ToolErrorCode.BUDGET_EXHAUSTED,
                f"tool call budget of {self._call_budget} calls exhausted",
            )
            return budget_error, None

        try:
            validated = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            invalid = ToolError.create(
                ToolErrorCode.INVALID_ARGUMENTS,
                f"invalid arguments for '{tool.name}'",
                field_errors=_field_errors(exc),
            )
            return invalid, None

        fault: InjectedFault | None = None
        try:
            if self._interceptor is None:
                raw_output = tool.invoke(validated)
            else:
                interception = self._interceptor.intercept(
                    tool, validated, lambda: tool.invoke(validated)
                )
                fault = interception.fault
                if interception.error is not None:
                    return interception.error, fault
                raw_output = interception.raw
        except ToolTimeoutError as exc:
            return ToolError.create(ToolErrorCode.TIMEOUT, str(exc) or "tool call timed out"), fault
        except ToolExecutionError as exc:
            return exc.error, fault

        try:
            return tool.output_model.model_validate(raw_output), fault
        except ValidationError as exc:
            malformed = ToolError.create(
                ToolErrorCode.MALFORMED_RESPONSE,
                f"'{tool.name}' returned a response that does not match its output schema",
                field_errors=_field_errors(exc),
            )
            return malformed, fault
