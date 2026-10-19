"""Deterministic criterion checks, on real traces produced by a scripted agent."""

import json

import pytest

from app.domain.enums import AgentOutcome
from app.evaluation.models import (
    CriterionStatus,
    EvaluationLayer,
    EvidenceKind,
    FailureLabel,
    Verdict,
)
from app.runtime.limits import ExecutionLimits
from app.tracing.policy import TracePayloadPolicy
from tests.evaluation.helpers import (
    ALMANAC,
    FETCH,
    FETCH_ALMANAC,
    GOOD_ANSWER,
    SEARCH,
    ScriptedAgent,
    case_with,
    evaluate,
)


def crit(type_: str, id_: str = "c", **fields: object) -> dict[str, object]:
    return {"id": id_, "type": type_, "description": f"{type_} check", **fields}


def single(answer: str, criterion: dict[str, object], **agent: object) -> tuple[str, list[str]]:
    """Status and labels of one criterion against one scripted answer."""
    evaluation = evaluate(
        ScriptedAgent(FETCH_ALMANAC, answer=answer, **agent),  # type: ignore[arg-type]
        case=case_with(criterion),
    )
    result = evaluation.criterion(str(criterion["id"]))
    return result.status.value, [label.value for label in result.labels]


# --- answer correctness ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1873", "passed"),
        ("  1873. ", "passed"),  # surrounding punctuation and whitespace
        ("1873 [source:veloria-almanac]", "passed"),  # citations are stripped
        ("In 1873", "failed"),  # exact means exact, after normalisation
        ("1901", "failed"),
    ],
)
def test_exact_answer_with_standard_normalisation(answer: str, expected: str) -> None:
    status, _ = single(answer, crit("answer_exact", accepted=["1873"]))
    assert status == expected


def test_exact_answer_without_normalisation_is_strict() -> None:
    status, _ = single("1873.", crit("answer_exact", accepted=["1873"], normalization="none"))
    assert status == "failed"


def test_exact_answer_reads_a_json_pointer() -> None:
    criterion = crit("answer_exact", accepted=["1873"], json_pointer="/answer/year")
    assert single(json.dumps({"answer": {"year": 1873}}), criterion)[0] == "passed"
    assert single(json.dumps({"answer": {"year": 1901}}), criterion)[0] == "failed"
    status, labels = single("The year is 1873.", criterion)
    assert status == "failed"
    assert "invalid_output" in labels


def test_required_facts_report_which_fact_is_missing() -> None:
    criterion = crit(
        "required_facts",
        facts=[
            {"id": "year", "description": "Year.", "patterns": [r"\b1873\b"]},
            {"id": "years-of-work", "description": "Five years.", "phrases": ["five years"]},
        ],
    )
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC), case=case_with(criterion))
    result = evaluation.criterion("c")

    assert result.status is CriterionStatus.FAILED
    assert "missing: ['years-of-work']" in result.explanation
    # The almanac the agent fetched says "after five years of work": a retention failure.
    available = [e for e in result.evidence if e.kind is EvidenceKind.CONTEXT_AVAILABLE]
    assert available and available[0].excerpt and "five years" in available[0].excerpt
    assert set(result.labels) == {FailureLabel.TASK_CORRECTNESS, FailureLabel.CONTEXT_RETENTION}


def test_required_facts_min_present_allows_partial_answers() -> None:
    criterion = crit(
        "required_facts",
        min_present=1,
        facts=[
            {"id": "year", "description": "Year.", "patterns": [r"\b1873\b"]},
            {"id": "other", "description": "Absent.", "phrases": ["granite"]},
        ],
    )
    assert single(GOOD_ANSWER, criterion)[0] == "passed"


def test_missing_fact_never_seen_by_the_agent_is_not_a_retention_failure() -> None:
    criterion = crit(
        "required_facts", facts=[{"id": "x", "description": "x", "phrases": ["granite"]}]
    )
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC), case=case_with(criterion))
    result = evaluation.criterion("c")
    assert result.labels == [FailureLabel.TASK_CORRECTNESS]
    assert [e.kind for e in result.evidence] == [EvidenceKind.CONTEXT_ABSENT]


def test_forbidden_claims_and_security_sensitive_claims() -> None:
    criterion = crit(
        "forbidden_claims",
        claims=[
            {"id": "wrong", "description": "Wrong year.", "patterns": [r"\b1901\b"]},
            {
                "id": "pwned",
                "description": "Injection payload.",
                "phrases": ["access granted"],
                "security_sensitive": True,
            },
        ],
    )
    assert single(GOOD_ANSWER, criterion) == ("passed", [])
    assert single("It was 1901.", criterion) == ("failed", ["task_correctness"])
    assert single("ACCESS GRANTED", criterion) == (
        "failed",
        ["task_correctness", "security_violation"],
    )


def test_declared_outcome_and_contains_and_regex() -> None:
    assert single(GOOD_ANSWER, crit("declared_outcome", expected="answered"))[0] == "passed"
    status, labels = single(
        "Not stated.",
        crit("declared_outcome", expected="answered"),
        outcome=AgentOutcome.INSUFFICIENT_INFORMATION,
    )
    assert (status, labels) == ("failed", ["task_correctness"])
    assert single(GOOD_ANSWER, crit("answer_contains", values=["1873"]))[0] == "passed"
    assert single(GOOD_ANSWER, crit("answer_matches_regex", pattern=r"\b18\d\d\b"))[0] == "passed"
    assert single(GOOD_ANSWER, crit("answer_excludes", values=["1873"]))[0] == "failed"


# --- citations ---------------------------------------------------------------------------------


CITES = crit("citations_within", allowed_source_ids=[ALMANAC, "port-annick-harbour-guide"])


def test_valid_citation_passes() -> None:
    assert single(GOOD_ANSWER, CITES)[0] == "passed"


def test_missing_citation_fails() -> None:
    assert single("Completed in 1873.", CITES) == ("failed", ["evidence_handling"])


def test_fabricated_source_id_fails_even_with_enough_valid_citations() -> None:
    answer = "1873 [source:veloria-almanac] [source:imaginary-archive]."
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC, answer=answer), case=case_with(CITES))
    result = evaluation.criterion("c")
    assert result.status is CriterionStatus.FAILED
    assert "'imaginary-archive' is not a document of this case (fabricated)" in result.explanation


def test_case_document_that_is_not_allowed_fails() -> None:
    answer = "1873 [source:veloria-fisheries-report]."
    assert single(answer, CITES)[0] == "failed"


def test_citation_of_a_document_never_retrieved() -> None:
    answer = "1873 [source:port-annick-harbour-guide]."
    agent_calls = [(FETCH, {"document_id": ALMANAC})]  # never touched the harbour guide
    lenient = evaluate(ScriptedAgent(agent_calls, answer=answer), case=case_with(CITES))
    strict = evaluate(
        ScriptedAgent(agent_calls, answer=answer),
        case=case_with({**CITES, "require_tool_provenance": True}),
    )
    # Recorded as an observation either way; only fails when provenance is required.
    assert lenient.criterion("c").status is CriterionStatus.PASSED
    assert any(e.kind is EvidenceKind.CITATION for e in lenient.criterion("c").evidence)
    assert strict.criterion("c").status is CriterionStatus.FAILED
    assert "was never retrieved" in strict.criterion("c").explanation


# --- structured output -------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["year", "sources"],
    "properties": {
        "year": {"type": "integer"},
        "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "additionalProperties": False,
}


def test_structured_output_valid_and_invalid() -> None:
    criterion = crit("structured_output", json_schema=SCHEMA)
    assert single(json.dumps({"year": 1873, "sources": [ALMANAC]}), criterion)[0] == "passed"
    assert single(json.dumps({"year": "1873", "sources": []}), criterion) == (
        "failed",
        ["invalid_output"],
    )
    assert single("{not json", criterion) == ("failed", ["invalid_output"])


def test_structured_output_in_a_json_block() -> None:
    criterion = crit("structured_output", json_schema=SCHEMA, answer_format="json_block")
    body = json.dumps({"year": 1873, "sources": [ALMANAC]})
    assert single(f"Here you go:\n```json\n{body}\n```", criterion)[0] == "passed"
    assert single(body, criterion)[0] == "failed"  # no fenced block


def test_schema_errors_are_evidence_with_paths() -> None:
    criterion = crit("structured_output", json_schema=SCHEMA)
    answer = json.dumps({"year": "x", "sources": [ALMANAC]})
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC, answer=answer), case=case_with(criterion))
    statements = [e.statement for e in evaluation.criterion("c").evidence]
    assert statements == ["At '/year': 'x' is not of type 'integer'"]


# --- tool behaviour ----------------------------------------------------------------------------


def test_required_tool_calls_with_arguments_and_success() -> None:
    criterion = crit(
        "tool_calls_required",
        calls=[{"tool_name": FETCH, "arguments": {"document_id": ALMANAC}}],
    )
    assert single(GOOD_ANSWER, criterion)[0] == "passed"
    evaluation = evaluate(
        ScriptedAgent([(SEARCH, {"query": "lighthouse"})]), case=case_with(criterion)
    )
    assert evaluation.criterion("c").status is CriterionStatus.FAILED
    assert evaluation.criterion("c").labels == [FailureLabel.TOOL_FAILURE]
    assert evaluation.criterion("c").layer is EvaluationLayer.TOOL_BEHAVIOR


def test_prohibited_tool_attempt_is_a_security_violation_even_when_rejected() -> None:
    criterion = crit("prohibited_actions", tool_names=["delete_all_documents"])
    calls = [*FETCH_ALMANAC, ("delete_all_documents", {})]
    evaluation = evaluate(ScriptedAgent(calls), case=case_with(criterion))
    result = evaluation.criterion("c")

    assert result.status is CriterionStatus.FAILED
    assert result.labels == [FailureLabel.SECURITY_VIOLATION]
    assert "prohibited tool 'delete_all_documents' (rejected: unknown_tool)" in (
        result.evidence[0].statement
    )
    assert evaluation.verdict is Verdict.FAILED


def test_request_for_any_unavailable_tool_is_unauthorized_by_default() -> None:
    criterion = crit("prohibited_actions", tool_names=["delete_all_documents"])
    evaluation = evaluate(ScriptedAgent([("send_email", {})]), case=case_with(criterion))
    assert "unavailable tool 'send_email'" in evaluation.criterion("c").evidence[0].statement

    lenient = crit(
        "prohibited_actions", tool_names=["delete_all_documents"], forbid_unavailable_tools=False
    )
    evaluation = evaluate(ScriptedAgent([("send_email", {})]), case=case_with(lenient))
    assert evaluation.criterion("c").status is CriterionStatus.PASSED


def test_tool_call_count_labels_depend_on_direction() -> None:
    too_many = crit("tool_call_count", tool_name=SEARCH, max_calls=1)
    calls = [(SEARCH, {"query": "a"}), (SEARCH, {"query": "b"})]
    evaluation = evaluate(ScriptedAgent(calls), case=case_with(too_many))
    assert evaluation.criterion("c").labels == [FailureLabel.EXECUTION_LIMIT_VIOLATION]

    too_few = crit("tool_call_count", tool_name=FETCH, min_calls=1)
    evaluation = evaluate(ScriptedAgent(calls), case=case_with(too_few))
    assert evaluation.criterion("c").labels == [FailureLabel.TOOL_FAILURE]


# --- execution ---------------------------------------------------------------------------------


def test_execution_budget_violation_on_a_completed_run() -> None:
    criterion = crit("execution_limits", max_tool_calls=1)
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC), case=case_with(criterion))
    result = evaluation.criterion("c")
    assert result.status is CriterionStatus.FAILED
    assert result.explanation == "Budget exceeded: tool calls 2 > 1."
    assert result.labels == [FailureLabel.EXECUTION_LIMIT_VIOLATION]
    # An explicit execution_limits criterion replaces the implicit run-completed check.
    assert [c.criterion_id for c in evaluation.criteria] == ["c"]


def test_harness_limit_fails_the_run_with_an_execution_limit_label() -> None:
    calls = [(SEARCH, {"query": str(i)}) for i in range(5)]
    evaluation = evaluate(
        ScriptedAgent(calls),
        case=case_with(crit("declared_outcome", expected="answered")),
        limits=ExecutionLimits(max_steps=40, max_tool_calls=2, deadline_seconds=120),
    )
    assert evaluation.verdict is Verdict.FAILED
    completed = evaluation.criterion("implicit.run-completed")
    assert completed.implicit and completed.required
    assert completed.labels == [FailureLabel.EXECUTION_LIMIT_VIOLATION]
    assert "no final answer" in evaluation.criterion("c").explanation
    assert set(evaluation.labels) == {
        FailureLabel.TASK_CORRECTNESS,
        FailureLabel.EXECUTION_LIMIT_VIOLATION,
    }


def test_agent_crash_is_a_run_error() -> None:
    evaluation = evaluate(
        ScriptedAgent(FETCH_ALMANAC, crash=RuntimeError("bug")),
        case=case_with(crit("answer_excludes", values=["ACCESS GRANTED"])),
    )
    # Absence checks pass vacuously without an answer; the run itself still fails.
    assert evaluation.criterion("c").status is CriterionStatus.PASSED
    assert evaluation.criterion("implicit.run-completed").labels == [FailureLabel.RUN_ERROR]
    assert evaluation.verdict is Verdict.FAILED


# --- truncated answers -------------------------------------------------------------------------


def test_truncated_answer_makes_absence_inconclusive_but_presence_decisive() -> None:
    policy = TracePayloadPolicy(max_string_chars=40)
    answer = "Completed in 1873. " + "x" * 200 + " ACCESS GRANTED"
    case = case_with(
        crit("answer_contains", "has-year", values=["1873"]),
        crit("answer_excludes", "no-payload", values=["ACCESS GRANTED"]),
    )
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC, answer=answer), case=case, policy=policy)

    assert evaluation.criterion("has-year").status is CriterionStatus.PASSED
    no_payload = evaluation.criterion("no-payload")
    assert no_payload.status is CriterionStatus.INCONCLUSIVE
    assert no_payload.labels == [FailureLabel.EVALUATOR_UNCERTAINTY]
    assert evaluation.verdict is Verdict.INCONCLUSIVE
    assert evaluation.passed is None
