"""Engine-level behaviour: scoring, layers, recovery, labels, and the sample scenarios."""

import pytest

from app.benchmarks.loader import load_bundled_suite
from app.domain.enums import CriterionType, RunStatus
from app.evaluation.engine import EvaluationInputError, Evaluator
from app.evaluation.models import (
    CaseEvaluation,
    CriterionEvaluation,
    CriterionStatus,
    EvaluationLayer,
    EvaluatorKind,
    FailureLabel,
    Verdict,
)
from app.evaluation.samples import EVALUATION_CASE, EVALUATION_SCENARIOS
from app.evaluation.scoring import decide
from app.runner.runner import RunRequest, ScenarioRunner
from app.runner.samples import (
    SAMPLE_AGENT_VERSION,
    SAMPLE_EXPERIMENT_ID,
    SAMPLE_LIMITS,
    deterministic_research_agent,
)
from app.tracing.collector import TraceCollector
from tests.evaluation.helpers import FETCH_ALMANAC, ScriptedAgent, evaluate, trace_for

# --- scoring rules -----------------------------------------------------------------------------


def _criterion(
    cid: str, status: CriterionStatus, *, weight: float = 1.0, required: bool = False
) -> CriterionEvaluation:
    labels = {
        CriterionStatus.PASSED: [],
        CriterionStatus.FAILED: [FailureLabel.TASK_CORRECTNESS],
        CriterionStatus.INCONCLUSIVE: [FailureLabel.EVALUATOR_UNCERTAINTY],
    }[status]
    return CriterionEvaluation(
        criterion_id=cid,
        criterion_type=CriterionType.ANSWER_CONTAINS,
        layer=EvaluationLayer.ANSWER,
        evaluator=EvaluatorKind.DETERMINISTIC,
        status=status,
        weight=weight,
        required=required,
        explanation="x",
        labels=labels,
    )


PASS, FAIL, OPEN = CriterionStatus.PASSED, CriterionStatus.FAILED, CriterionStatus.INCONCLUSIVE


@pytest.mark.parametrize(
    ("statuses", "required", "threshold", "verdict", "score", "upper"),
    [
        ([PASS, PASS], [], 1.0, Verdict.PASSED, 1.0, 1.0),
        ([PASS, FAIL], [], 0.5, Verdict.PASSED, 0.5, 0.5),  # optional failure within threshold
        ([PASS, FAIL], [1], 0.5, Verdict.FAILED, 0.5, 0.5),  # required failure always fails
        ([PASS, OPEN], [], 1.0, Verdict.INCONCLUSIVE, 0.5, 1.0),  # outcome depends on the unknown
        ([PASS, OPEN], [], 0.5, Verdict.PASSED, 0.5, 1.0),  # passes whatever the unknown is
        ([PASS, OPEN], [1], 0.5, Verdict.INCONCLUSIVE, 0.5, 1.0),  # required unknown blocks a pass
        ([FAIL, FAIL, OPEN], [], 0.5, Verdict.FAILED, 0.0, 1 / 3),  # fails whatever the unknown is
        ([FAIL, OPEN], [1], 0.1, Verdict.INCONCLUSIVE, 0.0, 0.5),
    ],
)
def test_verdict_is_decided_only_when_best_and_worst_case_agree(
    statuses: list[CriterionStatus],
    required: list[int],
    threshold: float,
    verdict: Verdict,
    score: float,
    upper: float,
) -> None:
    criteria = [
        _criterion(f"c{i}", status, required=i in required) for i, status in enumerate(statuses)
    ]
    decision = decide(criteria, threshold)
    assert (decision.verdict, decision.score, decision.upper_bound) == pytest.approx(
        (verdict, score, upper)
    )


def test_case_evaluation_rejects_a_verdict_that_contradicts_its_criteria() -> None:
    evaluation = evaluate(ScriptedAgent(FETCH_ALMANAC))
    assert evaluation.verdict is Verdict.PASSED
    with pytest.raises(ValueError, match="contradicts criteria"):
        evaluation.updated(verdict=Verdict.FAILED)
    with pytest.raises(ValueError, match="score does not match"):
        evaluation.updated(score=0.5)
    # Round-trips through JSON (e.g. for storage).
    assert CaseEvaluation.model_validate_json(evaluation.model_dump_json()) == evaluation


# --- the sample scenarios with the deterministic agent ----------------------------------------


def test_baseline_passes_every_layer() -> None:
    evaluation = evaluate(scenario="baseline")
    assert evaluation.verdict is Verdict.PASSED
    assert evaluation.score == 1.0
    assert evaluation.failure_labels == []
    assert [(layer.layer, layer.verdict) for layer in evaluation.layers] == [
        (EvaluationLayer.ANSWER, Verdict.PASSED),
        (EvaluationLayer.TOOL_BEHAVIOR, Verdict.PASSED),
        (EvaluationLayer.EXECUTION, Verdict.PASSED),
    ]
    assert evaluation.confidence is None  # purely deterministic
    assert evaluation.evaluator_version == "evaluator-0.1.0"
    assert (evaluation.case_id, evaluation.scenario_id) == (
        EVALUATION_CASE.id,
        EVALUATION_SCENARIOS["baseline"].id,
    )


def test_recovered_timeout_is_observed_but_does_not_fail_the_run() -> None:
    evaluation = evaluate(scenario="search-timeout-once")

    assert evaluation.verdict is Verdict.PASSED
    assert evaluation.failure_labels == []
    [failure] = evaluation.tool_failures
    assert (failure.tool_name, failure.status, failure.fault_ids) == (
        "search_documents",
        "timeout",
        ["t1"],
    )
    assert failure.recovered and failure.recovered_by_event_id is not None
    assert evaluation.criterion("recovers").status is CriterionStatus.PASSED


def test_the_same_timeout_without_a_retry_fails_only_the_recovery_and_answer_criteria() -> None:
    # One search that times out, then an answer from memory: answer layer and tool behaviour
    # are judged separately.
    agent = ScriptedAgent([("search_documents", {"query": "lighthouse"})])
    evaluation = evaluate(agent, scenario="search-timeout-once")

    recovery = evaluation.criterion("recovers")
    assert recovery.status is CriterionStatus.FAILED
    assert "no successful 'search_documents' call after fault 't1'" in recovery.explanation
    assert not evaluation.tool_failures[0].recovered
    layers = {layer.layer: layer.verdict for layer in evaluation.layers}
    assert layers[EvaluationLayer.TOOL_BEHAVIOR] is Verdict.FAILED
    # The answer text itself is right; only provenance-based citation checking fails it.
    assert evaluation.criterion("states-year").status is CriterionStatus.PASSED
    assert evaluation.criterion("cites-retrieved-sources").status is CriterionStatus.FAILED
    assert FailureLabel.TOOL_FAILURE in evaluation.labels


def test_expected_failure_that_never_happened_is_inconclusive() -> None:
    # search-flaky fires with probability 0.5; find a seed where it does not fire at all.
    scenario = EVALUATION_SCENARIOS["search-flaky"]
    recovery = {
        "id": "recovers",
        "type": "expected_recovery",
        "description": "Recovers from the flaky search.",
        "fault_ids": ["flaky"],
        "behavior": "retry_succeeded",
    }
    override = EVALUATION_CASE.expectation.model_dump()
    override["evaluation_criteria"].append(recovery)
    scenario = scenario.updated(expectation_override=override)
    results = {}
    for seed in range(8):
        evaluation = evaluate(scenario=scenario, seed=seed)
        results[seed] = (evaluation.criterion("recovers").status, evaluation.tool_failures)
    quiet = [s for s, (_, failures) in results.items() if not failures]
    fired = [s for s, (_, failures) in results.items() if failures]
    assert quiet and fired, "seeds 0..7 should include both outcomes"
    assert all(results[s][0] is CriterionStatus.INCONCLUSIVE for s in quiet)
    # When it fired, the criterion is decided: passed iff a later search succeeded (on some
    # seeds both the first try and the retry fail).
    for seed in fired:
        status, failures = results[seed]
        recovered = any(f.recovered for f in failures)
        assert status is (CriterionStatus.PASSED if recovered else CriterionStatus.FAILED)


def test_permanent_failure_expects_a_reported_failure_not_an_answer() -> None:
    evaluation = evaluate(scenario="search-timeout-always")
    assert evaluation.verdict is Verdict.PASSED
    assert len(evaluation.tool_failures) == 2  # first try + one retry, both timed out
    assert not any(f.recovered for f in evaluation.tool_failures)

    claims_answer = ScriptedAgent([("search_documents", {"query": "x"})])
    evaluation = evaluate(claims_answer, scenario="search-timeout-always")
    assert evaluation.verdict is Verdict.FAILED
    assert "declared 'answered' instead of reporting the failure" in (
        evaluation.criterion("reports-failure").explanation
    )


def test_retry_storm_violates_max_attempts() -> None:
    storm = ScriptedAgent([("search_documents", {"query": "x"})] * 5)
    evaluation = evaluate(storm, scenario="search-timeout-always")
    assert "5 identical 'search_documents' attempts" in (
        evaluation.criterion("reports-failure").explanation
    )


def test_bundled_dataset_run_errors_and_is_evaluated_as_a_run_error() -> None:
    """The Milestone 2 dataset cannot be run by the deterministic agent (no variables)."""
    suite = load_bundled_suite("research-agent-sample", "1.0.0")
    scenario = suite.get_scenario("research.fact-lookup.baseline")
    case = suite.get_case(scenario.case_id)
    request = RunRequest(
        experiment_id=SAMPLE_EXPERIMENT_ID,
        agent_version=SAMPLE_AGENT_VERSION,
        case=case,
        scenario=scenario,
        limits=SAMPLE_LIMITS,
        seed=0,
    )
    trace = TraceCollector().collect(ScenarioRunner(deterministic_research_agent).run(request))
    evaluation = Evaluator().evaluate(trace, case, scenario)

    assert evaluation.run_status is RunStatus.ERRORED
    assert evaluation.verdict is Verdict.FAILED
    assert FailureLabel.RUN_ERROR in evaluation.labels
    assert "TaskSpecificationError" in evaluation.criterion("implicit.run-completed").explanation


def test_evaluation_is_deterministic() -> None:
    first = evaluate(scenario="fetch-misleading").model_dump(exclude={"created_at"})
    second = evaluate(scenario="fetch-misleading").model_dump(exclude={"created_at"})
    assert first == second


def test_every_label_references_criteria_and_events() -> None:
    evaluation = evaluate(scenario="fetch-misleading")
    assert evaluation.verdict is Verdict.FAILED
    trace_ids = {e.id for e in trace_for(scenario="fetch-misleading").events}
    for assignment in evaluation.failure_labels:
        assert assignment.criterion_ids
        assert set(assignment.event_ids) <= trace_ids


# --- input validation --------------------------------------------------------------------------


def test_trace_of_another_scenario_is_rejected() -> None:
    trace = trace_for(scenario="baseline")
    with pytest.raises(EvaluationInputError, match="is for scenario"):
        Evaluator().evaluate(trace, EVALUATION_CASE, EVALUATION_SCENARIOS["search-empty"])


def test_scenario_of_another_case_is_rejected() -> None:
    suite = load_bundled_suite("research-agent-sample", "1.0.0")
    trace = trace_for(scenario="baseline")
    with pytest.raises(EvaluationInputError, match="is not a scenario of"):
        Evaluator().evaluate(
            trace, EVALUATION_CASE, suite.get_scenario("research.fact-lookup.baseline")
        )


def test_reserved_implicit_criterion_id_is_refused() -> None:
    from tests.evaluation.helpers import case_with

    case = case_with(
        {
            "id": "implicit.run-completed",
            "type": "declared_outcome",
            "description": "x",
            "expected": "answered",
        }
    )
    with pytest.raises(EvaluationInputError, match="reserved"):
        evaluate(ScriptedAgent(), case=case)
