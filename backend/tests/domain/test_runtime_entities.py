"""Agents, experiments, runs, traces, evaluations, classifications, and reviews."""

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.agent import AgentConfig, AgentVersion
from app.domain.enums import (
    AgentOutcome,
    ClassificationSource,
    ExperimentStatus,
    FailureCategory,
    ReviewTargetType,
    ReviewVerdict,
    RunStatus,
    Severity,
    ToolCallStatus,
)
from app.domain.evaluation import EvaluationResult, FailureClassification
from app.domain.experiment import Experiment, Run
from app.domain.ids import IdPrefix, new_id
from app.domain.review import HumanReviewAnnotation
from app.domain.trace import FinalAnswerEvent, RunTrace, ToolCallEvent, ToolCallRecord
from tests.domain.factories import at, ids


def _messages(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return " | ".join(err["msg"] for err in exc.value.errors())


# --- agents --------------------------------------------------------------------------------


def _agent_version(**overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "agent_config_id": new_id(IdPrefix.AGENT_CONFIG),
        "version_label": "v1",
        "model": {"provider": "mock", "name": "deterministic-echo"},
        "system_prompt": "You are a research assistant.",
        "tools": ["search_documents", "fetch_document"],
        "parameters": {"temperature": 0.0, "max_steps": 10, "timeout_seconds": 60},
        "created_at": at(0),
    }
    data.update(overrides)
    return data


def test_agent_config_and_version_are_linked_and_round_trip() -> None:
    config = AgentConfig(name="research-assistant", created_at=at(0))
    version = AgentVersion.model_validate(_agent_version(agent_config_id=config.id))

    assert version.agent_config_id == config.id
    assert AgentVersion.model_validate_json(version.model_dump_json()) == version


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tools": ["a", "a"]}, "duplicate tools: a"),
        ({"agent_config_id": new_id(IdPrefix.AGENT_VERSION)}, "String should match pattern"),
        (
            {"parameters": {"temperature": 3, "max_steps": 1, "timeout_seconds": 1}},
            "less than or equal to 2",
        ),
        ({"parameters": {"max_steps": 0, "timeout_seconds": 1}}, "greater than 0"),
        ({"model": {"provider": "mock"}}, "Field required"),
    ],
)
def test_invalid_agent_versions(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        AgentVersion.model_validate(_agent_version(**overrides))

    assert message in _messages(exc)


# --- experiments and runs ------------------------------------------------------------------


def _experiment(**overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "baseline vs faults",
        "suite_id": "research-agent-sample",
        "suite_version": "1.0.0",
        "agent_version_id": new_id(IdPrefix.AGENT_VERSION),
        "scenario_ids": ["research.fact-lookup.baseline"],
        "seed": 42,
        "created_at": at(0),
    }
    data.update(overrides)
    return data


def _run(**overrides: object) -> dict[str, Any]:
    generated = ids()
    data: dict[str, Any] = {
        "experiment_id": generated["experiment_id"],
        "case_id": "research.fact-lookup",
        "scenario_id": "research.fact-lookup.baseline",
        "agent_version_id": generated["agent_version_id"],
        "repetition": 0,
        "seed": 42,
        "created_at": at(0),
    }
    data.update(overrides)
    return data


def test_new_experiment_defaults_to_pending() -> None:
    experiment = Experiment.model_validate(_experiment())

    assert experiment.status is ExperimentStatus.PENDING
    assert experiment.repetitions == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "running", "started_at": at(1)},
        {"status": "completed", "started_at": at(1), "finished_at": at(2)},
        {"status": "failed", "started_at": at(1), "finished_at": at(1)},
        {"status": "cancelled", "finished_at": at(1)},
    ],
)
def test_valid_experiment_lifecycles(overrides: dict[str, Any]) -> None:
    Experiment.model_validate(_experiment(**overrides))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"started_at": at(1)}, "must not have started or finished"),
        ({"status": "running"}, "requires a start timestamp"),
        (
            {"status": "running", "started_at": at(1), "finished_at": at(2)},
            "must not have a finish",
        ),
        ({"status": "completed", "started_at": at(1)}, "requires a finish timestamp"),
        ({"status": "completed", "finished_at": at(1)}, "requires a start timestamp"),
        ({"status": "completed", "started_at": at(2), "finished_at": at(1)}, "created <= started"),
        ({"status": "running", "started_at": at(-1)}, "created <= started"),
        ({"status": "paused"}, "Input should be"),
        ({"repetitions": 0}, "greater than or equal to 1"),
        ({"scenario_ids": ["a", "a"]}, "duplicate scenario_ids: a"),
        ({"scenario_ids": []}, "at least 1 item"),
        ({"agent_version_id": new_id(IdPrefix.AGENT_CONFIG)}, "String should match pattern"),
    ],
)
def test_invalid_experiments(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Experiment.model_validate(_experiment(**overrides))

    assert message in _messages(exc)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "errored", "started_at": at(1), "finished_at": at(2)}, "must include error"),
        (
            {
                "status": "completed",
                "started_at": at(1),
                "finished_at": at(2),
                "error": {"error_type": "X", "message": "y"},
            },
            "must not include error details",
        ),
        ({"status": "timed_out", "started_at": at(1)}, "requires a finish timestamp"),
        ({"repetition": -1}, "greater than or equal to 0"),
        ({"experiment_id": new_id(IdPrefix.RUN)}, "String should match pattern"),
    ],
)
def test_invalid_runs(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Run.model_validate(_run(**overrides))

    assert message in _messages(exc)


def test_run_status_transitions_use_validated_updates() -> None:
    run = Run.model_validate(_run())

    # Records are immutable: in-place assignment is rejected and leaves the record unchanged...
    with pytest.raises(ValidationError, match="frozen"):
        run.status = RunStatus.RUNNING  # type: ignore[misc]
    assert run.status is RunStatus.QUEUED

    # ...and an invalid transition is rejected even when applied atomically...
    with pytest.raises(ValidationError, match="requires a start timestamp"):
        run.updated(status=RunStatus.RUNNING)

    # ...while updated() applies a valid multi-field transition as a new, validated record.
    running = run.updated(status=RunStatus.RUNNING, started_at=at(1))
    done = running.updated(status=RunStatus.COMPLETED, finished_at=at(5))

    assert done.id == run.id
    assert (done.started_at, done.finished_at) == (at(1), at(5))
    with pytest.raises(ValidationError):
        done.updated(finished_at=at(0))


# --- traces --------------------------------------------------------------------------------


def _tool_call(run_id: str, **overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": run_id,
        "tool_name": "search_documents",
        "arguments": {"query": "lighthouse", "limit": 3, "filters": {"lang": ["en"]}},
        "status": "success",
        "output": [{"id": "veloria-almanac", "score": 0.9}],
        "started_at": at(1),
        "finished_at": at(1.25),
    }
    data.update(overrides)
    return data


def _trace(run_id: str) -> dict[str, Any]:
    call_id = new_id(IdPrefix.TOOL_CALL)
    return {
        "run_id": run_id,
        "events": [
            {
                "type": "model_call",
                "run_id": run_id,
                "sequence": 0,
                "timestamp": at(0.5),
                "model": "mock",
                "input_tokens": 120,
                "output_tokens": 30,
                "latency_ms": 12.5,
            },
            {
                "type": "tool_call",
                "run_id": run_id,
                "sequence": 1,
                "timestamp": at(1),
                "tool_call": _tool_call(
                    run_id,
                    id=call_id,
                    status="timeout",
                    output=None,
                    injected_fault_id="first-timeout",
                ),
            },
            {
                "type": "fault_injected",
                "run_id": run_id,
                "sequence": 2,
                "timestamp": at(1),
                "fault_id": "first-timeout",
                "fault_type": "tool_timeout",
                "tool_call_id": call_id,
            },
            {
                "type": "agent_message",
                "run_id": run_id,
                "sequence": 3,
                "timestamp": at(2),
                "content": "Search timed out, retrying.",
            },
            {
                "type": "final_answer",
                "run_id": run_id,
                "sequence": 4,
                "timestamp": at(3),
                "content": "1873 [source:veloria-almanac]",
                "declared_outcome": "answered",
            },
        ],
    }


def test_valid_trace_round_trips_with_typed_events() -> None:
    run_id = new_id(IdPrefix.RUN)
    trace = RunTrace.model_validate(_trace(run_id))

    restored = RunTrace.model_validate_json(trace.model_dump_json())

    assert restored == trace
    assert isinstance(restored.events[1], ToolCallEvent)
    assert restored.tool_calls[0].status is ToolCallStatus.TIMEOUT
    final = restored.final_answer
    assert isinstance(final, FinalAnswerEvent)
    assert final.declared_outcome is AgentOutcome.ANSWERED


def test_tool_call_latency_is_derived_and_not_serialised() -> None:
    record = ToolCallRecord.model_validate(_tool_call(new_id(IdPrefix.RUN)))

    assert record.latency_ms == pytest.approx(250.0)
    assert "latency_ms" not in record.model_dump()
    assert ToolCallRecord.model_validate_json(record.model_dump_json()) == record


def _mutated_trace(mutate: str) -> dict[str, Any]:
    run_id = new_id(IdPrefix.RUN)
    trace = _trace(run_id)
    events = trace["events"]
    if mutate == "gap":
        events[3]["sequence"] = 7
    elif mutate == "final-not-last":
        events[3], events[4] = events[4], events[3]
        events[3]["sequence"], events[4]["sequence"] = 3, 4
        events[4]["timestamp"] = at(3)
    elif mutate == "two-finals":
        events.append({**events[4], "sequence": 5})
    elif mutate == "foreign-run":
        events[3]["run_id"] = new_id(IdPrefix.RUN)
    elif mutate == "unknown-tool-call":
        events[2]["tool_call_id"] = new_id(IdPrefix.TOOL_CALL)
    elif mutate == "fault-not-recorded":
        events[1]["tool_call"]["injected_fault_id"] = None
    elif mutate == "time-travel":
        events[3]["timestamp"] = at(0)
    elif mutate == "tool-call-other-run":
        events[1]["tool_call"]["run_id"] = new_id(IdPrefix.RUN)
    return trace


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ("gap", "sequence numbers must be 0, 1, 2"),
        ("final-not-last", "final_answer must be the last event"),
        ("two-finals", "at most one final_answer"),
        ("foreign-run", "events belong to other runs"),
        ("unknown-tool-call", "references unknown tool call"),
        ("fault-not-recorded", "does not record fault"),
        ("time-travel", "non-decreasing"),
        ("tool-call-other-run", "tool_call.run_id must match"),
    ],
)
def test_trace_integrity_rules(mutate: str, message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        RunTrace.model_validate(_mutated_trace(mutate))

    assert message in _messages(exc)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": "error"}, "must include an error_message"),
        ({"error_message": "boom"}, "must not include an error_message"),
        ({"status": "timeout"}, "must not include output"),
        ({"finished_at": at(0)}, "must not be before started_at"),
        ({"arguments": {"q": {1, 2}}}, "not a valid JSON value"),
    ],
)
def test_invalid_tool_calls(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ToolCallRecord.model_validate(_tool_call(new_id(IdPrefix.RUN), **overrides))

    assert message in _messages(exc)


# --- evaluations and classifications --------------------------------------------------------


def _criterion(
    criterion_id: str, *, passed: bool, weight: float = 1.0, required: bool = False
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "criterion_type": "answer_contains",
        "passed": passed,
        "weight": weight,
        "required": required,
    }


def _evaluation(**overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "run_id": new_id(IdPrefix.RUN),
        "evaluator_version": "0.1.0",
        "criterion_results": [
            _criterion("a", passed=True, weight=3.0, required=True),
            _criterion("b", passed=False, weight=1.0),
        ],
        "pass_threshold": 0.75,
        "score": 0.75,
        "passed": True,
        "created_at": at(10),
    }
    data.update(overrides)
    return data


def test_evaluation_score_and_pass_are_consistent() -> None:
    result = EvaluationResult.model_validate(_evaluation())

    assert result.passed
    assert EvaluationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"score": 0.5}, "does not match weighted criterion score 0.75"),
        ({"passed": False}, "passed does not match"),
        ({"pass_threshold": 0.8}, "passed does not match"),
        (
            {
                "criterion_results": [
                    _criterion("a", passed=False, weight=1, required=True),
                    _criterion("b", passed=True, weight=3),
                ],
                "score": 0.75,
                "passed": True,
            },
            "passed does not match",
        ),
        (
            {
                "criterion_results": [_criterion("a", passed=True), _criterion("a", passed=True)],
                "score": 1.0,
            },
            "duplicate criterion ids: a",
        ),
        ({"criterion_results": [], "score": 0.0}, "at least 1 item"),
    ],
)
def test_invalid_evaluations(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        EvaluationResult.model_validate(_evaluation(**overrides))

    assert message in _messages(exc)


def test_failure_classification_rules() -> None:
    run_id = new_id(IdPrefix.RUN)
    base = {
        "run_id": run_id,
        "category": "fabricated_citation",
        "severity": "high",
        "rationale": "Cited a document that was not provided.",
        "evidence_event_ids": [new_id(IdPrefix.TRACE_EVENT)],
        "created_at": at(11),
    }

    rule = FailureClassification.model_validate(
        {**base, "source": "rule", "evaluation_result_id": new_id(IdPrefix.EVALUATION_RESULT)}
    )
    human = FailureClassification.model_validate({**base, "source": "human"})

    assert rule.category is FailureCategory.FABRICATED_CITATION
    assert rule.severity is Severity.HIGH
    assert human.source is ClassificationSource.HUMAN
    with pytest.raises(ValidationError, match="must reference an evaluation result"):
        FailureClassification.model_validate({**base, "source": "rule"})
    with pytest.raises(ValidationError, match="Input should be"):
        FailureClassification.model_validate({**base, "source": "human", "category": "oops"})


# --- human review --------------------------------------------------------------------------


def _review(**overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "target_type": "failure_classification",
        "target_id": new_id(IdPrefix.FAILURE_CLASSIFICATION),
        "reviewer": "reviewer@example.com",
        "verdict": "disagree",
        "corrected_failure_category": "hallucinated_content",
        "created_at": at(20),
    }
    data.update(overrides)
    return data


def test_valid_review_annotation() -> None:
    review = HumanReviewAnnotation.model_validate(_review())

    assert review.target_type is ReviewTargetType.FAILURE_CLASSIFICATION
    assert review.verdict is ReviewVerdict.DISAGREE
    assert HumanReviewAnnotation.model_validate_json(review.model_dump_json()) == review


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target_id": new_id(IdPrefix.RUN)}, "must be a 'fcl_' ID"),
        ({"target_id": "research.fact-lookup"}, "must be a 'fcl_' ID"),
        (
            {"target_type": "run", "target_id": new_id(IdPrefix.RUN)},
            "corrected_failure_category requires a failure_classification target",
        ),
        (
            {"corrected_failure_category": None, "corrected_passed": False},
            "corrected_passed requires an evaluation_result target",
        ),
        ({"corrected_failure_category": None}, "needs a comment or a correction"),
        ({"updated_at": at(19)}, "must not be before created_at"),
    ],
)
def test_invalid_review_annotations(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as exc:
        HumanReviewAnnotation.model_validate(_review(**overrides))

    assert message in _messages(exc)


def test_disagree_with_comment_only_is_valid() -> None:
    review = HumanReviewAnnotation.model_validate(
        _review(
            corrected_failure_category=None,
            comment="Citation was actually valid.",
            updated_at=at(20) + timedelta(minutes=5),
        )
    )

    assert review.comment == "Citation was actually valid."
