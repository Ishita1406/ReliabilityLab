"""Scenario fault injection, layered between the ``ToolExecutor`` and the (unmodified) tools.

``FaultInjector`` implements the executor's ``CallInterceptor`` hook and interprets the
benchmark schema's ``FaultConfig`` objects. It never changes a tool's implementation or fixture
data; it decides, per call, whether to short-circuit the call or to transform a *copy* of the
tool's output.

Selection (deterministic):
* Calls are counted per target tool, 1-based, counting only calls that passed argument
  validation (invalid-argument calls never reach a tool or an interceptor).
* A fault is *eligible* on a call if ``apply_on_calls`` is null or contains the call number.
* Each fault owns a ``random.Random`` seeded by ``derive_seed(run_seed, scenario_id, fault_id)``
  and draws once per eligible call; it *fires* if the draw is below ``probability``
  (``probability = 1.0`` always fires). Each fault's stream is therefore independent of the
  others.
* At most one fault is applied per call: the first firing fault in declaration order. (The
  trace schema records a single ``injected_fault_id`` per tool call.)

Semantics per fault type: see ``docs/engineering/04-scenario-runner.md``.
"""

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Protocol, assert_never

from pydantic import BaseModel, JsonValue, ValidationError

from app.domain.enums import FaultType, MalformationKind
from app.domain.faults import (
    EmptyResultFault,
    FaultConfig,
    IncompleteResultFault,
    IncorrectOutputFault,
    LatencyFault,
    MalformedOutputFault,
    PromptInjectionFault,
    RateLimitFault,
    ToolErrorFault,
    ToolTimeoutFault,
)
from app.runtime.clock import Clock
from app.tools.base import Tool, ToolError, ToolErrorCode
from app.tools.executor import InjectedFault, Interception
from app.tools.fetch import FetchOutput
from app.tools.search import SearchOutput

CONTENT_FAULT_TYPES = frozenset(
    {
        FaultType.EMPTY_RESULT,
        FaultType.INCOMPLETE_RESULT,
        FaultType.INCORRECT_OUTPUT,
        FaultType.PROMPT_INJECTION,
    }
)
"""Fault types that need to understand a tool's output shape (a ``ContentAdapter``)."""


class FaultConfigurationError(ValueError):
    """A fault cannot be applied as configured (e.g. unsupported for its target tool)."""


MAX_SAFE_SEED_BITS = 53
"""Derived seeds fit in 53 bits so they survive JSON round trips through JavaScript numbers."""


def derive_seed(seed: int, *parts: str) -> int:
    """Stable seed from a base seed and labels (independent of ``PYTHONHASHSEED``)."""
    digest = hashlib.sha256("\x1f".join([str(seed), *parts]).encode()).digest()
    return int.from_bytes(digest[:8], "big") >> (64 - MAX_SAFE_SEED_BITS)


class _NotApplicableError(Exception):
    """The tool's raw output did not match its contract, so a content fault cannot apply."""


# --- content adapters ----------------------------------------------------------------------


class ContentAdapter(Protocol):
    def empty(self, tool_name: str) -> JsonValue | ToolError: ...
    def truncate(self, raw: JsonValue, keep_fraction: float) -> JsonValue: ...
    def replace(self, raw: JsonValue, text: str) -> JsonValue: ...
    def inject(self, raw: JsonValue, text: str) -> JsonValue: ...


def _parse[ModelT: BaseModel](model: type[ModelT], raw: JsonValue) -> ModelT:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise _NotApplicableError(str(exc)) from exc


def _kept_items(count: int, keep_fraction: float) -> int:
    """At least one item survives, and at least one is dropped whenever there are two or more."""
    if count <= 1:
        return count
    return min(count - 1, max(1, math.floor(count * keep_fraction)))


class SearchContentAdapter:
    def empty(self, tool_name: str) -> JsonValue:
        return {"results": []}

    def truncate(self, raw: JsonValue, keep_fraction: float) -> JsonValue:
        output = _parse(SearchOutput, raw)
        kept = _kept_items(len(output.results), keep_fraction)
        return SearchOutput(results=output.results[:kept]).model_dump(mode="json")

    def replace(self, raw: JsonValue, text: str) -> JsonValue:
        output = _parse(SearchOutput, raw)
        hits = [hit.model_copy(update={"snippet": text}) for hit in output.results]
        return SearchOutput(results=hits).model_dump(mode="json")

    def inject(self, raw: JsonValue, text: str) -> JsonValue:
        output = _parse(SearchOutput, raw)
        hits = [
            hit.model_copy(update={"snippet": f"{hit.snippet} {text}"}) for hit in output.results
        ]
        return SearchOutput(results=hits).model_dump(mode="json")


class FetchContentAdapter:
    def empty(self, tool_name: str) -> ToolError:
        return ToolError.create(ToolErrorCode.NOT_FOUND, "document not found", http_status=404)

    def truncate(self, raw: JsonValue, keep_fraction: float) -> JsonValue:
        document = _parse(FetchOutput, raw).document
        kept = max(1, math.floor(len(document.content) * keep_fraction))
        return FetchOutput(
            document=document.model_copy(update={"content": document.content[:kept]})
        ).model_dump(mode="json")

    def replace(self, raw: JsonValue, text: str) -> JsonValue:
        document = _parse(FetchOutput, raw).document
        return FetchOutput(document=document.model_copy(update={"content": text})).model_dump(
            mode="json"
        )

    def inject(self, raw: JsonValue, text: str) -> JsonValue:
        document = _parse(FetchOutput, raw).document
        content = f"{document.content}\n\n{text}"
        return FetchOutput(document=document.model_copy(update={"content": content})).model_dump(
            mode="json"
        )


CONTENT_ADAPTERS: dict[str, ContentAdapter] = {
    "search_documents": SearchContentAdapter(),
    "fetch_document": FetchContentAdapter(),
}


def validate_faults(faults: Sequence[FaultConfig], available_tools: Sequence[str]) -> None:
    """Reject faults that could never apply in this run."""
    for fault in faults:
        if fault.target_tool not in available_tools:
            raise FaultConfigurationError(
                f"fault '{fault.id}' targets '{fault.target_tool}', which is not available in "
                f"this run (available: {', '.join(available_tools) or 'none'})"
            )
        if fault.type in CONTENT_FAULT_TYPES and fault.target_tool not in CONTENT_ADAPTERS:
            raise FaultConfigurationError(
                f"fault '{fault.id}' of type '{fault.type}' is not supported for tool "
                f"'{fault.target_tool}' (no content adapter)"
            )


# --- error mapping -------------------------------------------------------------------------


def error_code_for_status(status: int | None) -> ToolErrorCode:
    """HTTP status of an injected ``tool_error`` -> tool error code."""
    if status is None or status >= 500:
        return ToolErrorCode.UNAVAILABLE
    if status == 429:
        return ToolErrorCode.RATE_LIMITED
    if status == 404:
        return ToolErrorCode.NOT_FOUND
    if status == 408:
        return ToolErrorCode.TIMEOUT
    return ToolErrorCode.INVALID_ARGUMENTS


def _malform(raw: JsonValue, kind: MalformationKind) -> JsonValue:
    text = json.dumps(raw)
    if kind is MalformationKind.TRUNCATED:
        return text[: max(1, len(text) // 2)]
    if kind is MalformationKind.INVALID_JSON:
        return text.replace('"', "'")
    return {"data": json.loads(text)}  # WRONG_SCHEMA: valid JSON, unexpected shape (a copy)


# --- injector ------------------------------------------------------------------------------


class FaultInjector:
    def __init__(
        self,
        faults: Sequence[FaultConfig],
        *,
        seed: int,
        scenario_id: str,
        clock: Clock,
    ) -> None:
        self._faults = list(faults)
        self._clock = clock
        self._rngs = {
            fault.id: random.Random(derive_seed(seed, scenario_id, fault.id))
            for fault in self._faults
        }
        self._calls: Counter[str] = Counter()

    @staticmethod
    def fault_seeds(
        faults: Sequence[FaultConfig], *, seed: int, scenario_id: str
    ) -> dict[str, int]:
        return {fault.id: derive_seed(seed, scenario_id, fault.id) for fault in faults}

    def intercept(
        self, tool: Tool, arguments: BaseModel, invoke: Callable[[], JsonValue]
    ) -> Interception:
        self._calls[tool.name] += 1
        call_number = self._calls[tool.name]

        selected: FaultConfig | None = None
        for fault in self._faults:
            if fault.target_tool != tool.name:
                continue
            if fault.apply_on_calls is not None and call_number not in fault.apply_on_calls:
                continue
            fires = self._rngs[fault.id].random() < fault.probability
            if fires and selected is None:
                selected = fault

        if selected is None:
            return Interception(raw=invoke())
        return self._apply(selected, tool, invoke)

    def _apply(
        self, fault: FaultConfig, tool: Tool, invoke: Callable[[], JsonValue]
    ) -> Interception:
        marker = InjectedFault(fault_id=fault.id, fault_type=fault.type)

        # Pre-call faults: the real tool is not invoked.
        if isinstance(fault, ToolTimeoutFault):
            self._clock.sleep(fault.timeout_ms / 1000)
            error = ToolError.create(
                ToolErrorCode.TIMEOUT, f"{tool.name} timed out after {fault.timeout_ms} ms"
            )
            return Interception(error=error, fault=marker)
        if isinstance(fault, RateLimitFault):
            error = ToolError.create(
                ToolErrorCode.RATE_LIMITED,
                f"429 Too Many Requests: retry after {fault.retry_after_ms} ms",
                retry_after_ms=fault.retry_after_ms,
                http_status=429,
            )
            return Interception(error=error, fault=marker)
        if isinstance(fault, ToolErrorFault):
            error = ToolError.create(
                error_code_for_status(fault.status_code),
                fault.error_message,
                http_status=fault.status_code,
            )
            return Interception(error=error, fault=marker)
        if isinstance(fault, EmptyResultFault):
            empty = CONTENT_ADAPTERS[tool.name].empty(tool.name)
            if isinstance(empty, ToolError):
                return Interception(error=empty, fault=marker)
            return Interception(raw=empty, fault=marker)

        # Latency: delay, then the real call.
        if isinstance(fault, LatencyFault):
            self._clock.sleep(fault.delay_ms / 1000)
            return Interception(raw=invoke(), fault=marker)

        # Post-call faults: invoke, then transform a copy of the output.
        raw = invoke()
        if isinstance(fault, MalformedOutputFault):
            return Interception(raw=_malform(raw, fault.malformation), fault=marker)

        adapter = CONTENT_ADAPTERS[tool.name]
        try:
            if isinstance(fault, IncompleteResultFault):
                transformed = adapter.truncate(raw, fault.keep_fraction)
            elif isinstance(fault, IncorrectOutputFault):
                transformed = adapter.replace(raw, fault.replacement_output)
            elif isinstance(fault, PromptInjectionFault):
                transformed = adapter.inject(raw, fault.injected_text)
            else:
                assert_never(fault)
        except _NotApplicableError:
            # The tool's own output already violates its contract; leave it for the executor
            # to report as malformed and do not claim the fault was applied.
            return Interception(raw=raw)
        return Interception(raw=transformed, fault=marker)
