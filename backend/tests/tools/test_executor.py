from datetime import timedelta

import pytest
from pydantic import BaseModel, JsonValue

from app.agents.fixtures import CORPUS, FIXTURE_START
from app.domain.base import DomainModel
from app.domain.enums import ToolCallStatus
from app.domain.ids import IdPrefix, derived_id, new_id
from app.runtime.clock import ManualClock
from app.runtime.execution import (
    DecisionStep,
    ExecutionRecorder,
    RejectedToolRequestStep,
    ToolCallStep,
)
from app.tools.base import ToolErrorCode, ToolFailure, ToolSuccess, output_as
from app.tools.executor import ToolExecutor
from app.tools.fetch import FetchOutput, MockFetchTool
from app.tools.mock import MockScript, RespondOutcome, TimeoutOutcome
from app.tools.search import MockSearchTool, SearchOutput


class Harness:
    def __init__(
        self,
        *,
        search_script: MockScript | None = None,
        fetch_script: MockScript | None = None,
        call_budget: int | None = None,
    ) -> None:
        self.run_id = new_id(IdPrefix.RUN)
        self.clock = ManualClock(FIXTURE_START)
        self.recorder = ExecutionRecorder(self.run_id, self.clock)
        self.search = MockSearchTool(CORPUS, self.clock, search_script)
        self.fetch = MockFetchTool(CORPUS, self.clock, fetch_script)
        self.executor = ToolExecutor(
            [self.search, self.fetch], self.recorder, call_budget=call_budget
        )

    def only_record(self) -> ToolCallStep:
        (step,) = self.recorder.build().steps
        assert isinstance(step, ToolCallStep)
        return step


def test_successful_call_returns_typed_output_and_records_it() -> None:
    h = Harness()

    result = h.executor.call("search_documents", {"query": "lighthouse", "limit": 1})

    assert isinstance(result, ToolSuccess)
    output = output_as(result, SearchOutput)
    assert [hit.document_id for hit in output.results] == ["port-annick-harbour-guide"]
    record = h.only_record().tool_call
    assert record.status is ToolCallStatus.SUCCESS
    assert record.id == result.call_id == derived_id(IdPrefix.TOOL_CALL, h.run_id, "tool-call", "1")
    assert record.run_id == h.run_id
    assert record.arguments == {"query": "lighthouse", "limit": 1}
    assert record.output == output.model_dump(mode="json")
    assert record.latency_ms == pytest.approx(5.0)
    assert record.error_message is None


@pytest.mark.parametrize(
    ("arguments", "locations"),
    [
        ({}, ["query"]),
        ({"query": 7}, ["query"]),
        ({"query": "x", "limit": 0}, ["limit"]),
        ({"query": "x", "limit": "five"}, ["limit"]),
        ({"query": "x", "sort": "date"}, ["sort"]),
        ({"query": "", "limit": 100}, ["query", "limit"]),
    ],
)
def test_invalid_arguments_return_structured_error_without_invoking_tool(
    arguments: dict[str, JsonValue], locations: list[str]
) -> None:
    h = Harness()

    result = h.executor.call("search_documents", arguments)

    assert isinstance(result, ToolFailure)
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert not result.error.retryable
    assert [e.location for e in result.error.field_errors] == locations
    assert h.search.call_count == 0
    record = h.only_record().tool_call
    assert record.status is ToolCallStatus.ERROR
    assert record.arguments == arguments
    assert record.output == result.error.model_dump(mode="json")
    assert record.error_message is not None
    assert record.error_message.startswith("invalid_arguments:")


@pytest.mark.parametrize("name", ["web_search", "Search Docs!", ""])
def test_unknown_tool_is_rejected_and_logged_without_a_tool_call_record(name: str) -> None:
    h = Harness()

    result = h.executor.call(name, {"q": "x"})

    assert isinstance(result, ToolFailure)
    assert result.call_id is None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL
    assert "fetch_document, search_documents" in result.error.message
    (step,) = h.recorder.build().steps
    assert isinstance(step, RejectedToolRequestStep)
    assert step.requested_name == name
    assert step.arguments == {"q": "x"}


def test_malformed_response_is_a_retryable_error() -> None:
    h = Harness(search_script=MockScript(default=RespondOutcome(payload={"hits": "oops"})))

    result = h.executor.call("search_documents", {"query": "x"})

    assert isinstance(result, ToolFailure)
    assert result.error.code is ToolErrorCode.MALFORMED_RESPONSE
    assert result.error.retryable
    assert [e.location for e in result.error.field_errors] == ["results", "hits"]
    assert h.only_record().tool_call.status is ToolCallStatus.ERROR


def test_wrong_value_types_in_response_are_malformed() -> None:
    payload: JsonValue = {
        "results": [{"document_id": "Bad Id", "title": "t", "score": 2.0, "snippet": ""}]
    }
    h = Harness(search_script=MockScript(default=RespondOutcome(payload=payload)))

    result = h.executor.call("search_documents", {"query": "x"})

    assert isinstance(result, ToolFailure)
    assert result.error.code is ToolErrorCode.MALFORMED_RESPONSE


def test_timeout_is_recorded_with_timeout_status_and_no_output() -> None:
    h = Harness(fetch_script=MockScript(default=TimeoutOutcome(after_ms=1_000)))

    result = h.executor.call("fetch_document", {"document_id": "veloria-almanac"})

    assert isinstance(result, ToolFailure)
    assert result.error.code is ToolErrorCode.TIMEOUT
    assert result.error.retryable
    record = h.only_record().tool_call
    assert record.status is ToolCallStatus.TIMEOUT
    assert record.output is None
    assert record.latency_ms == pytest.approx(1_005.0)


def test_not_found_is_a_non_retryable_error_returned_to_the_agent() -> None:
    h = Harness()

    result = h.executor.call("fetch_document", {"document_id": "missing-doc"})

    assert isinstance(result, ToolFailure)
    assert result.error.code is ToolErrorCode.NOT_FOUND
    assert not result.error.retryable
    record = h.only_record().tool_call
    assert record.output == {
        "code": "not_found",
        "message": "document 'missing-doc' does not exist",
        "retryable": False,
        "retry_after_ms": None,
        "http_status": None,
        "field_errors": [],
    }


def test_call_budget_blocks_further_calls_without_invoking_tools() -> None:
    h = Harness(call_budget=2)

    first = h.executor.call("fetch_document", {"document_id": "veloria-almanac"})
    second = h.executor.call("fetch_document", {"document_id": "veloria-almanac"})
    third = h.executor.call("fetch_document", {"document_id": "veloria-almanac"})
    unknown = h.executor.call("nope", {})

    assert isinstance(first, ToolSuccess) and isinstance(second, ToolSuccess)
    assert isinstance(output_as(first, FetchOutput), FetchOutput)
    assert isinstance(third, ToolFailure)
    assert third.error.code is ToolErrorCode.BUDGET_EXHAUSTED
    assert h.fetch.call_count == 2
    assert isinstance(unknown, ToolFailure)
    assert h.executor.calls_made == 4


def test_execution_log_is_ordered_and_includes_notes() -> None:
    h = Harness()

    h.recorder.note("starting")
    h.executor.call("search_documents", {"query": "lighthouse"})
    h.executor.call("bogus", {})
    h.recorder.note("done")
    log = h.recorder.build()

    assert [type(s) for s in log.steps] == [
        DecisionStep,
        ToolCallStep,
        RejectedToolRequestStep,
        DecisionStep,
    ]
    assert log.finished_at - log.started_at == timedelta(milliseconds=5)
    assert type(log).model_validate_json(log.model_dump_json()) == log


def test_executor_rejects_bad_configuration() -> None:
    h = Harness()

    with pytest.raises(ValueError, match="duplicate tool names"):
        ToolExecutor([h.search, h.search], h.recorder)
    with pytest.raises(ValueError, match="call_budget must be positive"):
        ToolExecutor([h.search], h.recorder, call_budget=0)


class _EchoInput(DomainModel):
    text: str


class _CrashingTool:
    name = "crash"
    description = "Raises an unexpected exception."
    input_model = _EchoInput
    output_model = _EchoInput

    def invoke(self, arguments: BaseModel) -> JsonValue:
        raise RuntimeError("bug in tool")


def test_unexpected_tool_exceptions_propagate() -> None:
    h = Harness()
    executor = ToolExecutor([_CrashingTool()], h.recorder)

    with pytest.raises(RuntimeError, match="bug in tool"):
        executor.call("crash", {"text": "hi"})
