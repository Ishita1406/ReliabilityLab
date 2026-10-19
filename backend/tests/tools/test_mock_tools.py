from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.agents.fixtures import CORPUS, FIXTURE_START
from app.runtime.clock import ManualClock
from app.tools.base import ToolErrorCode, ToolExecutionError, ToolTimeoutError
from app.tools.documents import Document, DocumentCorpus
from app.tools.fetch import FetchInput, FetchOutput, MockFetchTool
from app.tools.mock import FailOutcome, MockScript, RespondOutcome, TimeoutOutcome
from app.tools.search import MockSearchTool, SearchInput, SearchOutput


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(FIXTURE_START)


def _search(tool: MockSearchTool, query: str, limit: int = 5) -> SearchOutput:
    return SearchOutput.model_validate(tool.invoke(SearchInput(query=query, limit=limit)))


# --- search --------------------------------------------------------------------------------


def test_search_ranks_by_term_overlap_then_id(clock: ManualClock) -> None:
    results = _search(MockSearchTool(CORPUS, clock), "Port Annick lighthouse completed").results

    assert [(hit.document_id, hit.score) for hit in results] == [
        ("port-annick-harbour-guide", 1.0),
        ("veloria-almanac", 1.0),
        ("port-annick-census", 0.5),
        ("veloria-fisheries-report", 0.5),
    ]


def test_search_is_deterministic_and_respects_limit(clock: ManualClock) -> None:
    tool = MockSearchTool(CORPUS, clock)

    first = _search(tool, "University of Veloria founded", limit=2)
    second = _search(tool, "University of Veloria founded", limit=2)

    assert first == second
    assert len(first.results) == 2


@pytest.mark.parametrize("query", ["the of and", "zzz unrelated"])
def test_search_without_matching_terms_returns_no_results(clock: ManualClock, query: str) -> None:
    assert _search(MockSearchTool(CORPUS, clock), query).results == []


def test_search_snippet_is_bounded(clock: ManualClock) -> None:
    corpus = DocumentCorpus(documents=[Document(id="long", title="Long", content="word " * 100)])

    (hit,) = _search(MockSearchTool(corpus, clock), "word").results

    assert len(hit.snippet) == 160


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 21},
        {"query": "x", "extra": 1},
        {"query": 42},
    ],
)
def test_search_input_validation(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchInput.model_validate(arguments)


# --- fetch ---------------------------------------------------------------------------------


def test_fetch_returns_the_document(clock: ManualClock) -> None:
    raw = MockFetchTool(CORPUS, clock).invoke(FetchInput(document_id="veloria-almanac"))

    output = FetchOutput.model_validate(raw)

    assert output.document == CORPUS.get("veloria-almanac")


def test_fetch_missing_document_raises_structured_not_found(clock: ManualClock) -> None:
    with pytest.raises(ToolExecutionError) as exc:
        MockFetchTool(CORPUS, clock).invoke(FetchInput(document_id="does-not-exist"))

    assert exc.value.error.code is ToolErrorCode.NOT_FOUND
    assert not exc.value.error.retryable


@pytest.mark.parametrize("document_id", ["", "Not A Slug", "../etc/passwd"])
def test_fetch_input_rejects_invalid_ids(document_id: str) -> None:
    with pytest.raises(ValidationError):
        FetchInput(document_id=document_id)


def test_corpus_rejects_duplicate_ids() -> None:
    doc = Document(id="a", title="A", content="a")

    with pytest.raises(ValidationError, match="duplicate document ids: a"):
        DocumentCorpus(documents=[doc, doc])


# --- scripted behaviour --------------------------------------------------------------------


def test_script_by_call_overrides_default_and_unscripted_calls_use_real_behaviour(
    clock: ManualClock,
) -> None:
    script = MockScript(
        by_call={2: RespondOutcome(payload={"results": []})},
        default=None,
    )
    tool = MockSearchTool(CORPUS, clock, script)

    first = _search(tool, "lighthouse")
    second = _search(tool, "lighthouse")
    third = _search(tool, "lighthouse")

    assert first.results and third.results
    assert second.results == []
    assert tool.call_count == 3


def test_script_default_applies_to_every_unlisted_call(clock: ManualClock) -> None:
    script = MockScript(
        by_call={3: RespondOutcome(payload={"results": []})},
        default=FailOutcome(code=ToolErrorCode.UNAVAILABLE, message="down"),
    )
    tool = MockSearchTool(CORPUS, clock, script)

    for _ in range(2):
        with pytest.raises(ToolExecutionError) as exc:
            tool.invoke(SearchInput(query="x"))
        assert exc.value.error.code is ToolErrorCode.UNAVAILABLE
        assert exc.value.error.retryable
    assert tool.invoke(SearchInput(query="x")) == {"results": []}


def test_respond_outcome_returns_payload_verbatim_even_if_malformed(clock: ManualClock) -> None:
    script = MockScript(default=RespondOutcome(payload={"hits": "not a list"}))

    assert MockSearchTool(CORPUS, clock, script).invoke(SearchInput(query="x")) == {
        "hits": "not a list"
    }


def test_fail_outcome_carries_retry_after(clock: ManualClock) -> None:
    script = MockScript(
        default=FailOutcome(
            code=ToolErrorCode.RATE_LIMITED, message="slow down", retry_after_ms=750
        )
    )

    with pytest.raises(ToolExecutionError) as exc:
        MockSearchTool(CORPUS, clock, script).invoke(SearchInput(query="x"))

    assert exc.value.error.retry_after_ms == 750


def test_timeout_outcome_advances_virtual_clock(clock: ManualClock) -> None:
    script = MockScript(default=TimeoutOutcome(after_ms=30_000))
    tool = MockFetchTool(CORPUS, clock, script, latency_ms=5)

    with pytest.raises(ToolTimeoutError, match="30000 ms"):
        tool.invoke(FetchInput(document_id="veloria-almanac"))

    assert clock.now() - FIXTURE_START == timedelta(milliseconds=30_005)


def test_latency_is_applied_per_call(clock: ManualClock) -> None:
    tool = MockFetchTool(CORPUS, clock, latency_ms=40)

    tool.invoke(FetchInput(document_id="veloria-almanac"))
    tool.invoke(FetchInput(document_id="veloria-almanac"))

    assert clock.now() - FIXTURE_START == timedelta(milliseconds=80)


def test_script_parses_from_json_with_string_call_numbers() -> None:
    script = MockScript.model_validate_json(
        '{"by_call": {"1": {"kind": "timeout", "after_ms": 10}},'
        ' "default": {"kind": "fail", "code": "unavailable", "message": "down"}}'
    )

    assert isinstance(script.outcome_for(1), TimeoutOutcome)
    assert isinstance(script.outcome_for(2), FailOutcome)


@pytest.mark.parametrize(
    "raw",
    [
        '{"by_call": {"0": {"kind": "timeout", "after_ms": 10}}}',
        '{"default": {"kind": "explode"}}',
        '{"default": {"kind": "fail", "code": "teapot", "message": "x"}}',
        '{"default": {"kind": "timeout", "after_ms": 0}}',
    ],
)
def test_invalid_scripts_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        MockScript.model_validate_json(raw)


def test_tool_rejects_input_of_the_wrong_model(clock: ManualClock) -> None:
    with pytest.raises(TypeError, match="search_documents expects SearchInput"):
        MockSearchTool(CORPUS, clock).invoke(FetchInput(document_id="veloria-almanac"))


def test_manual_clock_rules() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ManualClock(FIXTURE_START.replace(tzinfo=None))
    with pytest.raises(ValueError, match="backwards"):
        ManualClock(FIXTURE_START).advance(-1)
