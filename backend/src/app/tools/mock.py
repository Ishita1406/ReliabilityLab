"""Deterministic, scriptable behaviour shared by the mock tools.

A ``MockScript`` maps 1-based call numbers of one tool to a scripted outcome; ``default``
applies to every call without an explicit entry. Calls with no scripted outcome run the
tool's real behaviour against its fixture data. Outcomes:

* ``respond``: return ``payload`` verbatim as the raw tool response. A payload that does not
  match the tool's output schema is how malformed responses are simulated.
* ``fail``: raise a structured ``ToolError`` with the given code.
* ``timeout``: advance the clock by ``after_ms`` and raise ``ToolTimeoutError``.

This is basic configurable mock behaviour, not the scenario fault-injection engine.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, NonNegativeInt, PositiveInt

from app.domain.base import DomainModel, NonEmptyStr
from app.runtime.clock import Clock
from app.tools.base import ToolError, ToolErrorCode, ToolExecutionError, ToolTimeoutError


class RespondOutcome(DomainModel):
    kind: Literal["respond"] = "respond"
    payload: JsonValue


class FailOutcome(DomainModel):
    kind: Literal["fail"] = "fail"
    code: ToolErrorCode
    message: NonEmptyStr
    retry_after_ms: NonNegativeInt | None = None


class TimeoutOutcome(DomainModel):
    kind: Literal["timeout"] = "timeout"
    after_ms: PositiveInt


MockOutcome = Annotated[RespondOutcome | FailOutcome | TimeoutOutcome, Field(discriminator="kind")]


class MockScript(DomainModel):
    by_call: dict[PositiveInt, MockOutcome] = Field(default_factory=dict)
    default: MockOutcome | None = None

    def outcome_for(self, call_number: int) -> RespondOutcome | FailOutcome | TimeoutOutcome | None:
        return self.by_call.get(call_number, self.default)


class MockTool[InputT: BaseModel, OutputT: BaseModel]:
    """Base class: input type check, call counting, latency, and script handling."""

    name: str
    description: str
    input_model: type[InputT]
    output_model: type[OutputT]

    def __init__(self, clock: Clock, script: MockScript | None = None, latency_ms: int = 5) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        self._clock = clock
        self._script = script or MockScript()
        self._latency_ms = latency_ms
        self.call_count = 0

    def invoke(self, arguments: BaseModel) -> JsonValue:
        if not isinstance(arguments, self.input_model):
            raise TypeError(
                f"{self.name} expects {self.input_model.__name__}, got {type(arguments).__name__}"
            )
        self.call_count += 1
        self._clock.sleep(self._latency_ms / 1000)

        outcome = self._script.outcome_for(self.call_count)
        if outcome is None:
            return self.respond(arguments).model_dump(mode="json")
        if isinstance(outcome, RespondOutcome):
            return outcome.payload
        if isinstance(outcome, FailOutcome):
            raise ToolExecutionError(
                ToolError.create(
                    outcome.code, outcome.message, retry_after_ms=outcome.retry_after_ms
                )
            )
        self._clock.sleep(outcome.after_ms / 1000)
        raise ToolTimeoutError(f"{self.name} timed out after {outcome.after_ms} ms")

    def respond(self, arguments: InputT) -> OutputT:
        """Real (unscripted) behaviour against fixture data."""
        raise NotImplementedError
