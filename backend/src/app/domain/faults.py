"""Injected fault configurations.

Every fault targets a named tool and is selected by its ``type`` discriminator, so each fault
type carries only the parameters that apply to it. Injection is deterministic: a fault fires on
the listed 1-based call numbers of its target tool (or every call when ``apply_on_calls`` is
omitted), gated by ``probability`` drawn from the run's seed.
"""

from typing import Annotated, Literal

from pydantic import Field, PositiveInt, field_validator

from app.domain.base import DomainModel
from app.domain.enums import FaultType, MalformationKind
from app.domain.ids import Slug


class _FaultBase(DomainModel):
    id: Slug = Field(description="Identifier unique within its scenario.")
    target_tool: Slug = Field(description="Name of the tool whose calls are affected.")
    apply_on_calls: list[PositiveInt] | None = Field(
        default=None,
        description="1-based call numbers of the target tool to affect; null means every call.",
    )
    probability: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("apply_on_calls")
    @classmethod
    def _calls_unique_and_sorted(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("must be null or a non-empty list")
        if len(set(value)) != len(value):
            raise ValueError("call numbers must be unique")
        return sorted(value)


class ToolTimeoutFault(_FaultBase):
    type: Literal[FaultType.TOOL_TIMEOUT]
    timeout_ms: PositiveInt


class ToolErrorFault(_FaultBase):
    type: Literal[FaultType.TOOL_ERROR]
    error_message: str = Field(min_length=1)
    status_code: int | None = Field(default=None, ge=400, le=599)


class MalformedOutputFault(_FaultBase):
    type: Literal[FaultType.MALFORMED_OUTPUT]
    malformation: MalformationKind


class EmptyResultFault(_FaultBase):
    type: Literal[FaultType.EMPTY_RESULT]


class IncorrectOutputFault(_FaultBase):
    type: Literal[FaultType.INCORRECT_OUTPUT]
    replacement_output: str = Field(
        min_length=1, description="Plausible but wrong content returned instead of the real result."
    )


class LatencyFault(_FaultBase):
    type: Literal[FaultType.LATENCY]
    delay_ms: PositiveInt


class RateLimitFault(_FaultBase):
    type: Literal[FaultType.RATE_LIMIT]
    retry_after_ms: PositiveInt


class PromptInjectionFault(_FaultBase):
    type: Literal[FaultType.PROMPT_INJECTION]
    injected_text: str = Field(
        min_length=1, description="Adversarial instructions appended to the tool output."
    )


FaultConfig = Annotated[
    ToolTimeoutFault
    | ToolErrorFault
    | MalformedOutputFault
    | EmptyResultFault
    | IncorrectOutputFault
    | LatencyFault
    | RateLimitFault
    | PromptInjectionFault,
    Field(discriminator="type"),
]
