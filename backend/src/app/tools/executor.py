"""Executes tool calls on behalf of an agent.

The executor is the single boundary between an agent's decisions and tool side effects:

1. resolve the tool by name (unknown names are rejected and logged, not raised),
2. enforce the per-run call budget,
3. validate raw JSON arguments against the tool's input model,
4. invoke the tool and translate ``ToolExecutionError`` / ``ToolTimeoutError``,
5. validate the raw output against the tool's output model (``malformed_response`` if not),
6. record a ``ToolCallRecord`` for every call to a registered tool.

Agents receive a ``ToolResult`` and never see exceptions for expected tool failures.
Unexpected exceptions from a tool are bugs and propagate unchanged.
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, JsonValue, ValidationError

from app.domain.enums import ToolCallStatus
from app.domain.trace import ToolCallRecord
from app.runtime.execution import ExecutionRecorder, RejectedToolRequestStep, ToolCallStep
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


class ToolExecutor:
    def __init__(
        self,
        tools: Sequence[Tool],
        recorder: ExecutionRecorder,
        *,
        call_budget: int | None = None,
    ) -> None:
        names = [tool.name for tool in tools]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate tool names: {names}")
        if call_budget is not None and call_budget < 1:
            raise ValueError("call_budget must be positive")
        self._tools = {tool.name: tool for tool in tools}
        self._recorder = recorder
        self._call_budget = call_budget
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
        outcome = self._execute(tool, raw_arguments)
        finished_at = clock.now()

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
                started_at=started_at,
                finished_at=finished_at,
            )
            result = ToolSuccess(call_id=call_id, output=outcome)

        self._recorder.record(ToolCallStep(tool_call=record))
        return result

    def _execute(self, tool: Tool, arguments: dict[str, JsonValue]) -> BaseModel | ToolError:
        if self._call_budget is not None and self._calls_made > self._call_budget:
            return ToolError.create(
                ToolErrorCode.BUDGET_EXHAUSTED,
                f"tool call budget of {self._call_budget} calls exhausted",
            )

        try:
            validated = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolError.create(
                ToolErrorCode.INVALID_ARGUMENTS,
                f"invalid arguments for '{tool.name}'",
                field_errors=_field_errors(exc),
            )

        try:
            raw_output = tool.invoke(validated)
        except ToolTimeoutError as exc:
            return ToolError.create(ToolErrorCode.TIMEOUT, str(exc) or "tool call timed out")
        except ToolExecutionError as exc:
            return exc.error

        try:
            return tool.output_model.model_validate(raw_output)
        except ValidationError as exc:
            return ToolError.create(
                ToolErrorCode.MALFORMED_RESPONSE,
                f"'{tool.name}' returned a response that does not match its output schema",
                field_errors=_field_errors(exc),
            )
