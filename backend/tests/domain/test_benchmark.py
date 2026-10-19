import json
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.benchmark import (
    SCHEMA_VERSION,
    AnswerContainsCriterion,
    BenchmarkCase,
    BenchmarkSuite,
    Scenario,
)
from app.domain.enums import AgentOutcome, FaultType, ScenarioKind
from app.domain.faults import FaultConfig, PromptInjectionFault, ToolTimeoutFault
from tests.domain.factories import (
    baseline_scenario_dict,
    case_dict,
    fault_scenario_dict,
    suite_dict,
)


def _error_messages(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return " | ".join(f"{err['loc']}: {err['msg']}" for err in exc.value.errors())


# --- valid records -------------------------------------------------------------------------


def test_minimal_case_is_valid_and_parses_nested_types() -> None:
    case = BenchmarkCase.model_validate(case_dict())

    assert case.expectation.expected_behavior.outcome is AgentOutcome.ANSWERED
    assert isinstance(case.expectation.evaluation_criteria[1], AnswerContainsCriterion)
    assert case.expectation.scoring.pass_threshold == 1.0


def test_minimal_suite_is_valid() -> None:
    suite = BenchmarkSuite.model_validate(suite_dict())

    assert [s.kind for s in suite.scenarios] == [ScenarioKind.BASELINE, ScenarioKind.FAULT_INJECTED]
    assert suite.schema_version == SCHEMA_VERSION


def test_scenarios_inherit_the_case_expectation_unless_overridden() -> None:
    override = {
        "expected_behavior": {"outcome": "task_failed", "description": "Reports failure."},
        "evaluation_criteria": [
            {
                "id": "declares-failed",
                "type": "declared_outcome",
                "description": "Declares task_failed.",
                "expected": "task_failed",
            }
        ],
    }
    suite = BenchmarkSuite.model_validate(
        suite_dict(
            scenarios=[baseline_scenario_dict(), fault_scenario_dict(expectation_override=override)]
        )
    )
    case = suite.get_case("demo.lookup")

    assert suite.effective_expectation("demo.lookup.baseline") is case.expectation
    overridden = suite.effective_expectation("demo.lookup.timeout")
    assert overridden.expected_behavior.outcome is AgentOutcome.TASK_FAILED
    assert [s.id for s in suite.scenarios_for("demo.lookup")] == [
        "demo.lookup.baseline",
        "demo.lookup.timeout",
    ]


# --- missing / invalid fields --------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["id", "title", "task_description", "inputs", "expectation", "difficulty", "metadata"]
)
def test_case_requires_field(field: str) -> None:
    data = case_dict()
    del data[field]

    with pytest.raises(ValidationError) as exc:
        BenchmarkCase.model_validate(data)

    assert any(err["loc"] == (field,) and err["type"] == "missing" for err in exc.value.errors())


def test_metadata_must_state_whether_the_case_is_synthetic() -> None:
    with pytest.raises(ValidationError) as exc:
        BenchmarkCase.model_validate(case_dict(metadata={"provenance": "unit test"}))

    assert ("metadata", "synthetic") in [err["loc"] for err in exc.value.errors()]


@pytest.mark.parametrize(
    ("overrides", "expected_loc"),
    [
        ({"difficulty": "impossible"}, ("difficulty",)),
        ({"id": "Not A Slug"}, ("id",)),
        ({"tags": ["Bad Tag"]}, ("tags", 0)),
        ({"title": ""}, ("title",)),
        ({"unexpected": "field"}, ("unexpected",)),
    ],
)
def test_case_rejects_invalid_field(
    overrides: dict[str, Any], expected_loc: tuple[Any, ...]
) -> None:
    with pytest.raises(ValidationError) as exc:
        BenchmarkCase.model_validate(case_dict(**overrides))

    assert expected_loc in [err["loc"] for err in exc.value.errors()]


def _case_with_criteria(*criteria: dict[str, Any], **behavior: object) -> dict[str, Any]:
    expected_behavior = {"outcome": "answered", "description": "d", **behavior}
    return case_dict(
        expectation={"expected_behavior": expected_behavior, "evaluation_criteria": list(criteria)}
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (case_dict(tags=["a", "a"]), "duplicate tags: a"),
        (
            _case_with_criteria(
                {
                    "id": "x",
                    "type": "tool_call_count",
                    "description": "d",
                    "tool_name": "web",
                    "max_calls": 1,
                }
            ),
            "unknown tool 'web'",
        ),
        (
            _case_with_criteria(
                {
                    "id": "x",
                    "type": "citations_within",
                    "description": "d",
                    "allowed_source_ids": ["ghost-doc"],
                }
            ),
            "unknown documents: ghost-doc",
        ),
        (
            _case_with_criteria(
                {"id": "x", "type": "declared_outcome", "description": "d", "expected": "refused"}
            ),
            "expects outcome 'refused'",
        ),
        (
            _case_with_criteria(
                {"id": "x", "type": "answer_matches_regex", "description": "d", "pattern": "(["}
            ),
            "invalid regular expression",
        ),
        (
            _case_with_criteria(
                {
                    "id": "x",
                    "type": "tool_call_count",
                    "description": "d",
                    "tool_name": "search_documents",
                }
            ),
            "always passes",
        ),
        (
            _case_with_criteria(
                {
                    "id": "x",
                    "type": "tool_call_count",
                    "description": "d",
                    "tool_name": "search_documents",
                    "min_calls": 3,
                    "max_calls": 1,
                }
            ),
            "max_calls must be >= min_calls",
        ),
        (
            _case_with_criteria(
                {"id": "x", "type": "answer_contains", "description": "d", "values": ["a"]},
                {"id": "x", "type": "answer_excludes", "description": "d", "values": ["b"]},
            ),
            "duplicate criterion ids: x",
        ),
        (
            _case_with_criteria(
                {"id": "x", "type": "answer_contains", "description": "d", "values": ["a"]},
                outcome="insufficient_information",
                reference_answer="42",
            ),
            "reference_answer is only allowed",
        ),
        (
            _case_with_criteria(
                {
                    "id": "x",
                    "type": "answer_contains",
                    "description": "d",
                    "values": ["a"],
                    "weight": 0,
                }
            ),
            "greater than 0",
        ),
        (_case_with_criteria(), "at least 1 item"),
    ],
)
def test_case_cross_field_rules(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        BenchmarkCase.model_validate(data)

    assert message in _error_messages(exc)


def test_unknown_criterion_type_is_rejected() -> None:
    data = _case_with_criteria({"id": "x", "type": "llm_judge", "description": "d"})

    with pytest.raises(ValidationError) as exc:
        BenchmarkCase.model_validate(data)

    assert "does not match any of the expected tags" in _error_messages(exc)


# --- faults --------------------------------------------------------------------------------

FAULT_ADAPTER: TypeAdapter[FaultConfig] = TypeAdapter(FaultConfig)


def test_fault_discriminator_selects_the_typed_model() -> None:
    fault = FAULT_ADAPTER.validate_python(
        {"id": "f", "type": "prompt_injection", "target_tool": "t", "injected_text": "obey me"}
    )

    assert isinstance(fault, PromptInjectionFault)
    assert fault.type is FaultType.PROMPT_INJECTION
    assert fault.apply_on_calls is None
    assert fault.probability == 1.0


def test_fault_call_numbers_are_sorted() -> None:
    fault = FAULT_ADAPTER.validate_python(
        {
            "id": "f",
            "type": "tool_timeout",
            "target_tool": "t",
            "timeout_ms": 5,
            "apply_on_calls": [3, 1],
        }
    )

    assert isinstance(fault, ToolTimeoutFault)
    assert fault.apply_on_calls == [1, 3]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"id": "f", "type": "tool_timeout", "target_tool": "t"}, "Field required"),
        (
            {
                "id": "f",
                "type": "tool_error",
                "target_tool": "t",
                "error_message": "x",
                "status_code": 200,
            },
            "greater than or equal to 400",
        ),
        ({"id": "f", "type": "latency", "target_tool": "t", "delay_ms": 0}, "greater than 0"),
        (
            {"id": "f", "type": "empty_result", "target_tool": "t", "probability": 1.5},
            "less than or equal to 1",
        ),
        (
            {"id": "f", "type": "empty_result", "target_tool": "t", "apply_on_calls": []},
            "non-empty list",
        ),
        (
            {"id": "f", "type": "empty_result", "target_tool": "t", "apply_on_calls": [1, 1]},
            "unique",
        ),
        (
            {"id": "f", "type": "empty_result", "target_tool": "t", "apply_on_calls": [0]},
            "greater than 0",
        ),
        (
            {"id": "f", "type": "empty_result", "target_tool": "t", "timeout_ms": 5},
            "Extra inputs are not permitted",
        ),
        ({"id": "f", "type": "disk_full", "target_tool": "t"}, "does not match any"),
    ],
)
def test_invalid_faults(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        FAULT_ADAPTER.validate_python(data)

    assert message in _error_messages(exc)


# --- scenarios and suites ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (baseline_scenario_dict(faults=fault_scenario_dict()["faults"]), "must not inject faults"),
        (
            baseline_scenario_dict(
                expectation_override=case_dict()["expectation"],
            ),
            "must use the case expectation",
        ),
        (fault_scenario_dict(faults=[]), "at least one fault"),
        (
            fault_scenario_dict(faults=fault_scenario_dict()["faults"] * 2),
            "duplicate fault ids: first-timeout",
        ),
        (baseline_scenario_dict(kind="chaos"), "Input should be 'baseline' or 'fault_injected'"),
    ],
)
def test_scenario_rules(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Scenario.model_validate(data)

    assert message in _error_messages(exc)


def _second_case() -> dict[str, Any]:
    return case_dict(id="demo.other")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"cases": [case_dict(), case_dict()]}, "duplicate case ids: demo.lookup"),
        (
            {"scenarios": [baseline_scenario_dict(), baseline_scenario_dict()]},
            "duplicate scenario ids",
        ),
        (
            {"scenarios": [baseline_scenario_dict(), baseline_scenario_dict(case_id="ghost")]},
            "references unknown case 'ghost'",
        ),
        (
            {"cases": [case_dict(), _second_case()]},
            "case 'demo.other' must have exactly one baseline scenario, found 0",
        ),
        (
            {
                "scenarios": [
                    baseline_scenario_dict(),
                    baseline_scenario_dict(id="demo.lookup.baseline-2"),
                ]
            },
            "found 2",
        ),
        (
            {
                "scenarios": [
                    baseline_scenario_dict(),
                    fault_scenario_dict(
                        faults=[
                            {"id": "f", "type": "empty_result", "target_tool": "fetch_document"}
                        ]
                    ),
                ]
            },
            "targets tool 'fetch_document' which case 'demo.lookup' does not provide",
        ),
        (
            {
                "scenarios": [
                    baseline_scenario_dict(),
                    fault_scenario_dict(
                        expectation_override={
                            "expected_behavior": {"outcome": "task_failed", "description": "d"},
                            "evaluation_criteria": [
                                {
                                    "id": "c",
                                    "type": "citations_within",
                                    "description": "d",
                                    "allowed_source_ids": ["ghost"],
                                }
                            ],
                        }
                    ),
                ]
            },
            "unknown documents: ghost",
        ),
        ({"synthetic": False}, "must set synthetic: true"),
        ({"schema_version": "2.0"}, "Input should be '1.0'"),
        ({"version": "1.0"}, "String should match pattern"),
        ({"created_at": "2026-01-01T00:00:00"}, "timezone"),
        ({"cases": []}, "at least 1 item"),
    ],
)
def test_suite_relationship_rules(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        BenchmarkSuite.model_validate(suite_dict(**overrides))

    assert message in _error_messages(exc)


def test_lookup_helpers_raise_key_error_for_unknown_ids() -> None:
    suite = BenchmarkSuite.model_validate(suite_dict())

    with pytest.raises(KeyError):
        suite.get_case("missing")
    with pytest.raises(KeyError):
        suite.effective_expectation("missing")


# --- serialization -------------------------------------------------------------------------


def test_suite_round_trips_through_json() -> None:
    suite = BenchmarkSuite.model_validate(suite_dict())

    dumped = suite.model_dump_json()
    restored = BenchmarkSuite.model_validate_json(dumped)

    assert restored == suite
    assert restored.model_dump_json() == dumped


def test_suite_json_uses_plain_strings_for_enums_and_utc_z_timestamps() -> None:
    suite = BenchmarkSuite.model_validate(suite_dict())

    data = json.loads(suite.model_dump_json())

    assert data["created_at"] == "2026-01-01T00:00:00.000000Z"
    assert data["scenarios"][1]["kind"] == "fault_injected"
    assert data["scenarios"][1]["faults"][0]["type"] == "tool_timeout"
    assert data["cases"][0]["difficulty"] == "easy"


def test_explicit_defaults_are_serialised() -> None:
    case = BenchmarkCase.model_validate(case_dict())

    criterion = case.model_dump(mode="json")["expectation"]["evaluation_criteria"][1]

    assert criterion == {
        "id": "states-year",
        "description": "Contains the year.",
        "weight": 1.0,
        "required": False,
        "type": "answer_contains",
        "values": ["1873"],
        "match": "all",
        "case_sensitive": False,
    }
