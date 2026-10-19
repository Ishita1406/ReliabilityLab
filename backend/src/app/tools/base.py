"""The tool contract.

A tool has a name, a Pydantic input model, a Pydantic output model, and an ``invoke`` method.
``invoke`` receives already-validated input and returns *raw JSON*; the ``ToolExecutor``
validates that JSON against the output model. Returning raw JSON (rather than the model)
mirrors real tools, which return untrusted payloads, and lets malformed responses be caught
at one boundary.

Tools signal expected failures by raising ``ToolExecutionError`` (carrying a structured
``ToolError``) or ``ToolTimeoutError``. Any other exception is a bug and propagates.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, Field, JsonValue, NonNegativeInt

from app.domain.base import DomainModel, NonEmptyStr


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    BUDGET_EXHAUSTED = "budget_exhausted"


RETRYABLE_CODES = frozenset(
    {
        ToolErrorCode.TIMEOUT,
        ToolErrorCode.RATE_LIMITED,
        ToolErrorCode.UNAVAILABLE,
        ToolErrorCode.MALFORMED_RESPONSE,
    }
)
"""Errors that may succeed if the same call is repeated."""


class FieldError(DomainModel):
    location: NonEmptyStr = Field(description="Dotted path of the invalid argument.")
    message: NonEmptyStr


class ToolError(DomainModel):
    """Structured error returned to the agent instead of tool output."""

    code: ToolErrorCode
    message: NonEmptyStr
    retryable: bool
    retry_after_ms: NonNegativeInt | None = None
    http_status: int | None = Field(
        default=None, ge=400, le=599, description="HTTP status for HTTP-style tool failures."
    )
    field_errors: list[FieldError] = Field(default_factory=list)

    @classmethod
    def create(
        cls,
        code: ToolErrorCode,
        message: str,
        *,
        retry_after_ms: int | None = None,
        http_status: int | None = None,
        field_errors: list[FieldError] | None = None,
    ) -> Self:
        """Build an error whose ``retryable`` flag follows ``RETRYABLE_CODES``."""
        return cls(
            code=code,
            message=message,
            retryable=code in RETRYABLE_CODES,
            retry_after_ms=retry_after_ms,
            http_status=http_status,
            field_errors=field_errors or [],
        )


class ToolExecutionError(Exception):
    """Raised by a tool for an expected failure (not found, unavailable, ...)."""

    def __init__(self, error: ToolError) -> None:
        super().__init__(f"{error.code}: {error.message}")
        self.error = error


class ToolTimeoutError(Exception):
    """Raised by a tool when the call exceeded its time limit."""


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    @property
    def output_model(self) -> type[BaseModel]: ...

    def invoke(self, arguments: BaseModel) -> JsonValue:
        """Run the tool with validated ``arguments`` (an ``input_model`` instance)."""
        ...


@dataclass(frozen=True)
class ToolSuccess:
    call_id: str
    output: BaseModel


@dataclass(frozen=True)
class ToolFailure:
    call_id: str | None
    """``None`` when the request was rejected before reaching a tool (unknown tool name)."""
    error: ToolError


ToolResult = ToolSuccess | ToolFailure


def output_as[OutputT: BaseModel](result: ToolSuccess, model: type[OutputT]) -> OutputT:
    """Narrow a successful result's output to the expected model type."""
    if not isinstance(result.output, model):
        raise TypeError(f"expected {model.__name__}, got {type(result.output).__name__}")
    return result.output
