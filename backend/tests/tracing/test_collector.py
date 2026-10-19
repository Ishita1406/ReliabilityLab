"""Trace collection from scenario run results (no database)."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.agents.interface import (
    AgentDescriptor,
    AgentEnvironment,
    AgentOutput,
    AgentRunMetadata,
    AgentTask,
    FinalAnswer,
    StopReason,
)
from app.domain.enums import AgentOutcome, FaultType, RunStatus
from app.runner.runner import ScenarioRunner, ScenarioRunResult
from app.runner.samples import SCENARIOS, deterministic_research_agent, sample_request
from app.runtime.limits import ExecutionLimits
from app.tracing.collector import TraceCollector
from app.tracing.reconstruct import render_tree, tool_calls
from app.tracing.schema import CollectedTrace, EventKind, EventStatus

RUNNER = ScenarioRunner(deterministic_research_agent)
COLLECTOR = TraceCollector()


def collect(
    name: str, limits: ExecutionLimits | None = None
) -> tuple[ScenarioRunResult, CollectedTrace]:
    request = sample_request(name)
    if limits is not None:
        request = request.updated(limits=limits)
    result = RUNNER.run(request)
    return result, COLLECTOR.collect(result)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_tool_call_has_a_separate_result_child(name: str) -> None:
    result, trace = collect(name)

    calls = [e for e in trace.events if e.kind is EventKind.TOOL_CALL]
    assert len(calls) == len(result.execution.tool_calls)
    for call in calls:
        results = [e for e in trace.children(call.id) if e.kind is EventKind.TOOL_RESULT]
        assert len(results) == 1
        assert results[0].tool_call_id == call.tool_call_id
        assert results[0].sequence > call.sequence


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_tool_calls_are_reconstructed_exactly(name: str) -> None:
    result, trace = collect(name)

    assert tool_calls(trace) == result.execution.tool_calls


def test_trace_tree_shape_for_a_fault_scenario() -> None:
    _, trace = collect("fetch-rate-limited")

    root = trace.root
    assert root.kind is EventKind.RUN and root.parent_id is None
    assert (root.agent_name, root.model_provider) == ("deterministic-research", "deterministic")
    assert {e.parent_id for e in trace.events[1:]} >= {root.id}
    faults = [e for e in trace.events if e.kind is EventKind.FAULT_INJECTED]
    assert [(f.fault_id, f.fault_type) for f in faults] == [("rl", FaultType.RATE_LIMIT)]
    call = next(e for e in trace.events if e.id == faults[0].parent_id)
    result = next(e for e in trace.children(call.id) if e.kind is EventKind.TOOL_RESULT)
    assert (call.sequence, faults[0].sequence, result.sequence) == (
        call.sequence,
        call.sequence + 1,
        call.sequence + 2,
    )
    assert result.status is EventStatus.ERROR
    assert result.error_type == "rate_limited"
    assert isinstance(result.error_details, dict)
    assert result.error_details["http_status"] == 429
    assert trace.events[-1].kind is EventKind.FINAL_ANSWER
    assert trace.events[-1].attributes["declared_outcome"] == "answered"


def test_timeout_result_has_timeout_status_and_duration() -> None:
    _, trace = collect("search-timeout-once")

    call = next(e for e in trace.events if e.kind is EventKind.TOOL_CALL)
    result = next(e for e in trace.children(call.id) if e.kind is EventKind.TOOL_RESULT)
    assert result.status is EventStatus.TIMEOUT
    assert result.payload is None
    assert call.duration_ms == pytest.approx(10_000)
    assert trace.root.duration_ms == pytest.approx(10_220)


@pytest.mark.parametrize(
    ("scenario", "limits", "status", "event_status"),
    [
        (
            "baseline",
            ExecutionLimits(max_steps=40, max_tool_calls=2, deadline_seconds=120),
            RunStatus.LIMIT_EXCEEDED,
            EventStatus.ERROR,
        ),
        (
            "search-timeout-always",
            ExecutionLimits(max_steps=40, max_tool_calls=12, deadline_seconds=5),
            RunStatus.TIMED_OUT,
            EventStatus.TIMEOUT,
        ),
    ],
)
def test_failed_runs_end_with_a_run_error_event(
    scenario: str, limits: ExecutionLimits, status: RunStatus, event_status: EventStatus
) -> None:
    result, trace = collect(scenario, limits)

    assert result.run.status is status
    last = trace.events[-1]
    assert last.kind is EventKind.RUN_ERROR
    assert last.status is event_status
    assert trace.root.status is event_status
    assert not any(e.kind is EventKind.FINAL_ANSWER for e in trace.events)


def _scripted(behaviour: Callable[[AgentEnvironment], object]) -> ScenarioRunResult:
    class _Agent:
        @property
        def descriptor(self) -> AgentDescriptor:
            return AgentDescriptor(name="scripted", version="1")

        def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
            behaviour(environment)
            return AgentOutput(
                answer=FinalAnswer(content="done", declared_outcome=AgentOutcome.ANSWERED),
                metadata=AgentRunMetadata(stop_reason=StopReason.COMPLETED),
            )

    return ScenarioRunner(lambda _v: _Agent()).run(sample_request("baseline"))


def test_rejected_tool_request_is_a_tool_call_without_result() -> None:
    result = _scripted(lambda env: env.tools.call("Delete Everything!", {"really": True}))
    trace = COLLECTOR.collect(result)

    rejected = [e for e in trace.events if e.status is EventStatus.REJECTED]
    assert len(rejected) == 1
    assert rejected[0].kind is EventKind.TOOL_CALL
    assert rejected[0].tool_name == "Delete Everything!"
    assert rejected[0].tool_call_id is None
    assert rejected[0].error_type == "unknown_tool"
    assert trace.children(rejected[0].id) == []
    assert tool_calls(trace) == []


def test_secrets_in_arguments_and_notes_are_redacted() -> None:
    fake_key = "sk-test-0000000000000000FAKE"

    def leak(env: AgentEnvironment) -> None:
        env.note(f"using {fake_key}")
        env.tools.call("search_documents", {"query": "x", "api_key": fake_key})

    trace = COLLECTOR.collect(_scripted(leak))

    assert fake_key not in trace.model_dump_json()


def test_collection_is_deterministic() -> None:
    result, first = collect("fetch-misleading")

    assert COLLECTOR.collect(result) == first
    assert (
        first.model_dump_json()
        == COLLECTOR.collect(RUNNER.run(sample_request("fetch-misleading"))).model_dump_json()
    )


def test_render_tree_outlines_parent_child_structure() -> None:
    _, trace = collect("fetch-temporary-error")

    lines = render_tree(trace).splitlines()

    assert lines[0].startswith("#0 run ok")
    assert any(line.startswith("    #") and "fault_injected" in line for line in lines)
    assert any(line.startswith("  #") and "tool_call fetch_document" in line for line in lines)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda events: events.reverse(), "ordered by sequence"),
        (lambda events: events.pop(0), "ordered by sequence"),
    ],
)
def test_collected_trace_rejects_broken_order(
    mutate: Callable[[list[dict[str, object]]], object], message: str
) -> None:
    _, trace = collect("baseline")
    data = trace.model_dump(mode="json")
    mutate(data["events"])

    with pytest.raises(ValidationError, match=message):
        CollectedTrace.model_validate(data)


def test_collected_trace_rejects_result_under_wrong_parent() -> None:
    _, trace = collect("baseline")
    data = trace.model_dump(mode="json")
    result_event = next(e for e in data["events"] if e["kind"] == "tool_result")
    result_event["parent_id"] = data["events"][0]["id"]

    with pytest.raises(ValidationError, match="must be a child of its tool_call"):
        CollectedTrace.model_validate(data)
