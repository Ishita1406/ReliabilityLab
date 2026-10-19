"""Validation of the Milestone 6 criterion types in the benchmark schema."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain.benchmark import EvaluationCriterion, Scenario
from app.evaluation.samples import EVALUATION_CASE
from app.runner.samples import SCENARIOS

ADAPTER: TypeAdapter[Any] = TypeAdapter(EvaluationCriterion)


def criterion(type_: str, **fields: object) -> dict[str, object]:
    return {"id": "c", "type": type_, "description": "d", **fields}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (criterion("answer_exact", accepted=[]), "at least 1 item"),
        (criterion("answer_exact", accepted=["x"], json_pointer="no-slash"), "should match"),
        (
            criterion("required_facts", facts=[{"id": "f", "description": "d"}]),
            "needs at least one phrase or pattern",
        ),
        (
            criterion("required_facts", facts=[{"id": "f", "description": "d", "patterns": ["("]}]),
            "invalid regular expression",
        ),
        (
            criterion(
                "required_facts",
                min_present=2,
                facts=[{"id": "f", "description": "d", "phrases": ["x"]}],
            ),
            "min_present cannot exceed",
        ),
        (
            criterion(
                "forbidden_claims",
                claims=[
                    {"id": "a", "description": "d", "phrases": ["x"]},
                    {"id": "a", "description": "d", "phrases": ["y"]},
                ],
            ),
            "duplicate claim ids",
        ),
        (criterion("structured_output", json_schema={"type": "nonsense"}), "invalid JSON schema"),
        (
            criterion("prohibited_actions", forbid_unavailable_tools=False),
            "otherwise it always passes",
        ),
        (criterion("expected_recovery", fault_ids=[], behavior="retry_succeeded"), "at least 1"),
        (
            criterion(
                "rubric",
                pass_score=0.5,
                dimensions=[
                    {"id": "x", "description": "d", "levels": [{"score": 1, "description": "a"}]}
                ],
            ),
            "at least 2 items",
        ),
        (
            criterion(
                "rubric",
                pass_score=0.5,
                dimensions=[
                    {
                        "id": "x",
                        "description": "d",
                        "levels": [
                            {"score": 1, "description": "a"},
                            {"score": 1, "description": "b"},
                        ],
                    }
                ],
            ),
            "duplicate 'x' level scores",
        ),
    ],
)
def test_invalid_criteria_are_rejected(data: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ADAPTER.validate_python(data)


def test_required_tool_calls_must_reference_available_tools() -> None:
    expectation = EVALUATION_CASE.expectation.model_dump()
    expectation["evaluation_criteria"].append(
        criterion("tool_calls_required", calls=[{"tool_name": "send_email"}])
    )
    with pytest.raises(ValidationError, match="references unknown tools: send_email"):
        EVALUATION_CASE.updated(expectation=expectation)


def test_prohibited_tools_need_not_exist() -> None:
    expectation = EVALUATION_CASE.expectation.model_dump()
    expectation["evaluation_criteria"].append(
        criterion("prohibited_actions", tool_names=["Delete Everything!"])
    )
    EVALUATION_CASE.updated(expectation=expectation)


RECOVERY = criterion("expected_recovery", fault_ids=["t1"], behavior="retry_succeeded")


def test_expected_recovery_is_not_allowed_at_case_level() -> None:
    expectation = EVALUATION_CASE.expectation.model_dump()
    expectation["evaluation_criteria"].append(RECOVERY)
    with pytest.raises(ValidationError, match="only allowed in a fault-injected scenario"):
        EVALUATION_CASE.updated(expectation=expectation)


def test_expected_recovery_must_reference_the_scenarios_own_faults() -> None:
    expectation = EVALUATION_CASE.expectation.model_dump()
    expectation["evaluation_criteria"].append(RECOVERY)
    scenario = SCENARIOS["search-timeout-once"]
    assert scenario.updated(expectation_override=expectation).expectation_override is not None

    other = SCENARIOS["fetch-rate-limited"]
    with pytest.raises(ValidationError, match="references faults not in scenario"):
        Scenario.model_validate({**other.model_dump(), "expectation_override": expectation})


def test_milestone_2_criteria_keep_their_defaults() -> None:
    citations = ADAPTER.validate_python(criterion("citations_within", allowed_source_ids=["a"]))
    assert (citations.min_citations, citations.require_tool_provenance) == (1, False)
