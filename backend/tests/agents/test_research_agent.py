import re
from datetime import timedelta

import pytest

from app.agents import demo
from app.agents.fixtures import (
    FIXTURE_START,
    FIXTURES,
    LIGHTHOUSE_TASK,
    ResearchFixture,
)
from app.agents.interface import (
    AgentDescriptor,
    AgentEnvironment,
    AgentOutput,
    AgentResult,
    AgentRunMetadata,
    AgentTask,
    EvidenceStatus,
    FinalAnswer,
    StopReason,
    run_agent,
)
from app.agents.research import (
    DeterministicResearchAgent,
    ResearchAgentConfig,
    TaskSpecificationError,
)
from app.benchmarks.loader import load_bundled_suite
from app.domain.enums import AgentOutcome, ToolCallStatus
from app.domain.ids import IdPrefix, new_id
from app.domain.trace import FinalAnswerEvent
from app.runtime.execution import RejectedToolRequestStep
from app.tools.base import ToolErrorCode, ToolFailure
from app.tools.mock import FailOutcome, MockScript
from tests.agents.trace_adapter import to_run_trace


def _run(
    fixture: ResearchFixture,
    *,
    config: ResearchAgentConfig | None = None,
    call_budget: int | None = None,
    run_id: str | None = None,
) -> AgentResult:
    environment, _, _ = fixture.build(run_id or new_id(IdPrefix.RUN), call_budget=call_budget)
    return run_agent(DeterministicResearchAgent(config), fixture.task, environment)


def _calls(result: AgentResult, tool: str) -> list[ToolCallStatus]:
    return [c.status for c in result.execution.tool_calls if c.tool_name == tool]


# --- every fixture -------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_produces_expected_outcome(name: str) -> None:
    fixture = FIXTURES[name]

    result = _run(fixture)

    assert result.answer.declared_outcome is fixture.expected_outcome
    assert result.metadata.evidence_status is fixture.expected_evidence_status


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_runs_are_reproducible(name: str) -> None:
    run_id = new_id(IdPrefix.RUN)

    first = _run(FIXTURES[name], run_id=run_id)
    second = _run(FIXTURES[name], run_id=run_id)

    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_result_converts_to_a_valid_run_trace(name: str) -> None:
    result = _run(FIXTURES[name])

    trace = to_run_trace(result)

    assert trace.tool_calls == result.execution.tool_calls
    final = trace.final_answer
    assert isinstance(final, FinalAnswerEvent)
    assert final.declared_outcome is result.answer.declared_outcome


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_citations_in_content_match_declared_sources(name: str) -> None:
    result = _run(FIXTURES[name])

    assert result.answer.cited_ids == result.answer.source_ids


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_result_round_trips_through_json(name: str) -> None:
    result = _run(FIXTURES[name])

    assert AgentResult.model_validate_json(result.model_dump_json()) == result


# --- behaviour per scenario ----------------------------------------------------------------


def test_successful_research_compares_sources_and_cites_all_supporting_ones() -> None:
    result = _run(FIXTURES["successful-research"])

    assert result.answer.source_ids == ["port-annick-harbour-guide", "veloria-almanac"]
    assert "1873" in result.answer.content
    assert [(e.source_id, e.value) for e in result.metadata.evidence] == [
        ("port-annick-harbour-guide", "1873"),
        ("veloria-almanac", "1873"),
        ("port-annick-census", None),
    ]
    assert result.metadata.stop_reason is StopReason.COMPLETED
    assert _calls(result, "fetch_document") == [ToolCallStatus.SUCCESS] * 3


def test_conflicting_evidence_reports_every_value_without_choosing_one() -> None:
    result = _run(FIXTURES["conflicting-evidence"])
    content = result.answer.content

    assert "1902 [source:university-of-veloria-prospectus]" in content
    assert "1907 [source:university-charter-history]" in content
    assert "disagree" in content
    assert "cannot be given" in content
    assert result.answer.declared_outcome is not AgentOutcome.ANSWERED


def test_insufficient_evidence_does_not_invent_an_answer() -> None:
    result = _run(FIXTURES["insufficient-evidence"])

    assert result.answer.source_ids == []
    assert all(e.value is None for e in result.metadata.evidence)
    numbers = set(re.findall(r"\d[\d,]*", result.answer.content))
    assert numbers == {"1850"}  # only the year from the question itself


def test_missing_document_is_reported_and_remaining_evidence_used() -> None:
    result = _run(FIXTURES["missing-document"])

    assert result.metadata.unretrieved_source_ids == ["port-annick-archive"]
    assert result.answer.source_ids == ["veloria-almanac"]
    assert "could not retrieve port-annick-archive" in result.answer.content
    assert _calls(result, "fetch_document") == [ToolCallStatus.ERROR, ToolCallStatus.SUCCESS]


def test_malformed_response_is_retried_once_then_succeeds() -> None:
    result = _run(FIXTURES["malformed-response"])

    assert _calls(result, "search_documents") == [ToolCallStatus.ERROR, ToolCallStatus.SUCCESS]
    assert result.answer.declared_outcome is AgentOutcome.ANSWERED


def test_timeouts_exhaust_retries_and_fail_the_task() -> None:
    result = _run(FIXTURES["tool-timeout"])

    assert _calls(result, "fetch_document") == [ToolCallStatus.TIMEOUT] * 6
    assert result.metadata.stop_reason is StopReason.TOOL_FAILURE
    assert result.metadata.unretrieved_source_ids == [
        "port-annick-harbour-guide",
        "veloria-almanac",
        "port-annick-census",
    ]
    assert "timeout" in result.answer.content
    assert result.answer.source_ids == []


def test_unavailable_search_fails_without_fetching() -> None:
    result = _run(FIXTURES["search-unavailable"])

    assert _calls(result, "search_documents") == [ToolCallStatus.ERROR, ToolCallStatus.ERROR]
    assert _calls(result, "fetch_document") == []
    assert "503 Service Unavailable" in result.answer.content


def test_max_retries_zero_disables_retries() -> None:
    result = _run(FIXTURES["malformed-response"], config=ResearchAgentConfig(max_retries=0))

    assert _calls(result, "search_documents") == [ToolCallStatus.ERROR]
    assert result.answer.declared_outcome is AgentOutcome.TASK_FAILED


def test_rate_limit_retry_after_is_honoured() -> None:
    fixture = ResearchFixture(
        name="rate-limited",
        description="first search rate limited",
        task=LIGHTHOUSE_TASK,
        expected_outcome=AgentOutcome.ANSWERED,
        expected_evidence_status=EvidenceStatus.CONSISTENT,
        search_script=MockScript(
            by_call={
                1: FailOutcome(code=ToolErrorCode.RATE_LIMITED, message="429", retry_after_ms=1_500)
            }
        ),
    )

    result = _run(fixture)

    first, second = [c for c in result.execution.tool_calls if c.tool_name == "search_documents"]
    assert second.started_at - first.finished_at == timedelta(milliseconds=1_500)
    assert result.answer.declared_outcome is AgentOutcome.ANSWERED


def test_non_retryable_errors_are_not_retried() -> None:
    result = _run(FIXTURES["missing-document"])

    statuses = _calls(result, "fetch_document")
    assert statuses.count(ToolCallStatus.ERROR) == 1


def test_budget_exhaustion_stops_and_answers_from_partial_evidence() -> None:
    result = _run(FIXTURES["successful-research"], call_budget=2)

    assert result.metadata.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert result.answer.declared_outcome is AgentOutcome.ANSWERED
    assert result.answer.source_ids == ["port-annick-harbour-guide"]
    assert result.metadata.unretrieved_source_ids == ["veloria-almanac"]


def test_budget_exhausted_before_any_evidence_fails_the_task() -> None:
    result = _run(FIXTURES["successful-research"], call_budget=1)

    assert result.answer.declared_outcome is AgentOutcome.TASK_FAILED
    assert result.metadata.stop_reason is StopReason.BUDGET_EXHAUSTED


def test_retry_notes_are_in_the_execution_log_not_the_answer() -> None:
    result = _run(FIXTURES["malformed-response"])

    notes = [s.message for s in result.execution.steps if hasattr(s, "message")]
    assert any("retry 1/1" in note for note in notes)
    assert "retry" not in result.answer.content


# --- task specification -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"available_tools": ["search_documents"]}, "does not provide tools: fetch_document"),
        ({"variables": {"answer_pattern": "(x)"}}, "'search_query'"),
        ({"variables": {"search_query": "x"}}, "'answer_pattern' must be a string"),
        ({"variables": {"search_query": "x", "answer_pattern": "(["}}, "is invalid"),
        ({"variables": {"search_query": "x", "answer_pattern": r"\d+"}}, "one capture group"),
        ({"variables": {"search_query": "x", "answer_pattern": "(a)(b)"}}, "one capture group"),
        ({"variables": {"search_query": 5, "answer_pattern": "(a)"}}, "'search_query'"),
        (
            {"variables": {"search_query": "x", "answer_pattern": "(a)", "subject": " "}},
            "'subject'",
        ),
    ],
)
def test_invalid_task_specification_raises(changes: dict[str, object], message: str) -> None:
    fixture = FIXTURES["successful-research"]
    task = AgentTask.model_validate({**fixture.task.model_dump(), **changes})
    environment, _, _ = fixture.build(new_id(IdPrefix.RUN))

    with pytest.raises(TaskSpecificationError, match=message):
        DeterministicResearchAgent().run(task, environment)


def test_agent_task_from_benchmark_inputs_excludes_documents() -> None:
    suite = load_bundled_suite("research-agent-sample", "1.0.0")
    inputs = suite.get_case("research.fact-lookup").inputs

    task = AgentTask.from_task_inputs(inputs)

    assert task.prompt == inputs.prompt
    assert task.available_tools == inputs.available_tools
    assert "documents" not in AgentTask.model_fields


# --- interface contract ----------------------------------------------------------------------


class _ScriptedAgent:
    """A minimal non-deterministic-style agent: proves the protocol is not research-specific."""

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(name="scripted", version="1")

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
        environment.note("calling a tool that does not exist")
        result = environment.tools.call("browse_web", {"url": "https://example.com"})
        assert isinstance(result, ToolFailure)
        return AgentOutput(
            answer=FinalAnswer(
                content=f"Gave up: {result.error.code}.",
                declared_outcome=AgentOutcome.TASK_FAILED,
            ),
            metadata=AgentRunMetadata(stop_reason=StopReason.TOOL_FAILURE),
        )


def test_protocol_supports_other_agents_and_logs_rejected_tool_requests() -> None:
    environment, _, _ = FIXTURES["successful-research"].build(new_id(IdPrefix.RUN))

    result = run_agent(_ScriptedAgent(), LIGHTHOUSE_TASK, environment)

    assert result.agent.name == "scripted"
    assert result.execution.tool_calls == []
    assert any(isinstance(s, RejectedToolRequestStep) for s in result.execution.steps)
    to_run_trace(result)  # still a valid trace


def test_agent_result_rejects_tool_calls_from_another_run() -> None:
    result = _run(FIXTURES["successful-research"])

    with pytest.raises(ValueError, match="another run"):
        result.updated(run_id=new_id(IdPrefix.RUN))


def test_final_answer_rules() -> None:
    answer = FinalAnswer(
        content="A [source:a] and [source:b], again [source:a].",
        declared_outcome=AgentOutcome.ANSWERED,
        source_ids=["a", "b"],
    )

    assert answer.cited_ids == ["a", "b"]
    with pytest.raises(ValueError, match="duplicate source_ids: a"):
        FinalAnswer(content="x", declared_outcome=AgentOutcome.ANSWERED, source_ids=["a", "a"])


def test_execution_times_come_from_the_injected_clock() -> None:
    result = _run(FIXTURES["successful-research"])

    assert result.execution.started_at == FIXTURE_START
    assert result.execution.finished_at == FIXTURE_START + timedelta(milliseconds=20)


# --- demo CLI -----------------------------------------------------------------------------


def test_demo_runs_every_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main([]) == 0

    out = capsys.readouterr().out
    for name, fixture in FIXTURES.items():
        assert f"=== {name}:" in out
        assert f"(expected {fixture.expected_outcome})" in out


def test_demo_json_output_is_a_valid_agent_result(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--json", "conflicting-evidence"]) == 0

    AgentResult.model_validate_json(capsys.readouterr().out)


def test_demo_rejects_unknown_fixture() -> None:
    with pytest.raises(SystemExit) as exc:
        demo.main(["nope"])

    assert exc.value.code == 2
