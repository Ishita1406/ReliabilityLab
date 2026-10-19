"""Regression policy rules, threshold boundaries, and independent security hard failures."""

import pytest

from app.experiments.comparison import ExperimentComparison, compare
from app.experiments.policy import PolicyEvaluation, RegressionPolicy, RuleStatus, evaluate_policy
from tests.experiments.helpers import SCENARIOS, F, P, U, results, uniform

TEN = SCENARIOS[:10]


def comparison(
    candidate_failures: int,
    *,
    total: int = 10,
    unauthorized: dict[str, bool] | None = None,
    candidate_outcome: str = F,
) -> ExperimentComparison:
    base = uniform(P, total)
    cand = {
        s: [candidate_outcome] if i < candidate_failures else [P] for i, s in enumerate(TEN[:total])
    }
    return compare(
        results("base", base, unauthorized={s: False for s in base} if unauthorized else None),
        results("cand", cand, unauthorized=unauthorized),
    )


def gate(comparison: ExperimentComparison, **policy: object) -> PolicyEvaluation:
    return evaluate_policy(comparison, RegressionPolicy.model_validate(policy))


LENIENT: dict[str, object] = {"max_success_rate_drop": 1.0, "max_newly_failing_cases": None}


# --- success rate tolerance ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "tolerance", "status"),
    [
        (1, 0.1, RuleStatus.PASSED),  # drop 1/10 == tolerance: inclusive
        (1, 0.09, RuleStatus.FAILED),
        (2, 0.1, RuleStatus.FAILED),
        (3, 0.3, RuleStatus.PASSED),  # 1 - 0.7 is 0.30000000000000004 in floats; exact here
        (0, 0.0, RuleStatus.PASSED),
        (1, 0.0, RuleStatus.FAILED),
    ],
)
def test_success_drop_boundaries(failures: int, tolerance: float, status: RuleStatus) -> None:
    result = gate(
        comparison(failures), max_success_rate_drop=tolerance, max_newly_failing_cases=None
    )
    assert result.status("success_rate_drop") is status
    assert result.passed is (status is RuleStatus.PASSED)


def test_improvements_never_fail_the_success_rule() -> None:
    improved = compare(results("b", {TEN[0]: [F]}), results("c", {TEN[0]: [P]}))
    assert gate(improved).status("success_rate_drop") is RuleStatus.PASSED


def test_significance_requirement_downgrades_small_drops_to_warnings() -> None:
    one_of_ten = gate(
        comparison(1),
        max_success_rate_drop=0.0,
        require_significance_for_success_drop=True,
        max_newly_failing_cases=None,
    )
    assert one_of_ten.status("success_rate_drop") is RuleStatus.WARNING
    assert one_of_ten.passed
    rule = next(r for r in one_of_ten.rules if r.rule == "success_rate_drop")
    assert "p=1" in rule.message and "not significant" in rule.message

    six_of_ten = gate(
        comparison(6),
        max_success_rate_drop=0.0,
        require_significance_for_success_drop=True,
        max_newly_failing_cases=None,
    )
    assert six_of_ten.status("success_rate_drop") is RuleStatus.FAILED  # p = 0.03125


def test_without_the_significance_requirement_the_tolerance_decides() -> None:
    result = gate(comparison(1), max_success_rate_drop=0.0, max_newly_failing_cases=None)
    assert result.status("success_rate_drop") is RuleStatus.FAILED
    message = next(r.message for r in result.rules if r.rule == "success_rate_drop")
    assert "not significant" in message  # the report never claims significance it lacks


# --- newly failing cases ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failures", "limit", "status"), [(1, 1, "passed"), (2, 1, "failed"), (0, 0, "passed")]
)
def test_newly_failing_limit_boundary(failures: int, limit: int, status: str) -> None:
    result = gate(comparison(failures), max_success_rate_drop=None, max_newly_failing_cases=limit)
    assert result.status("newly_failing_cases") == status


# --- security hard failures ---------------------------------------------------------------------


UNAUTHORIZED = {TEN[0]: True, **{s: False for s in TEN[1:]}}


def test_unauthorized_action_fails_regardless_of_tolerances() -> None:
    result = gate(comparison(1, unauthorized=UNAUTHORIZED), **LENIENT)
    assert result.status("unauthorized_action") is RuleStatus.FAILED
    assert result.status("security_violation") is RuleStatus.FAILED
    assert {r.rule for r in result.rules if r.hard} == {"unauthorized_action", "security_violation"}
    assert result.failed_rules == ["unauthorized_action", "security_violation"]


@pytest.mark.parametrize(
    ("unauthorized_on", "violation_on", "failed"),
    [
        (True, False, ["unauthorized_action"]),
        (False, True, ["security_violation"]),
        (False, False, []),
    ],
)
def test_security_rules_are_independently_configurable(
    unauthorized_on: bool, violation_on: bool, failed: list[str]
) -> None:
    result = gate(
        comparison(1, unauthorized=UNAUTHORIZED),
        **LENIENT,
        fail_on_unauthorized_action=unauthorized_on,
        fail_on_security_violation=violation_on,
    )
    assert result.failed_rules == failed
    assert result.passed is (not failed)


def test_unmeasured_unauthorized_actions_are_reported_as_unavailable() -> None:
    result = gate(comparison(0))
    assert result.status("unauthorized_action") is RuleStatus.UNAVAILABLE
    assert result.passed  # 'warn' by default
    strict = gate(comparison(0), unavailable_metric="fail")
    assert strict.status("unauthorized_action") is RuleStatus.FAILED
    assert not strict.passed


# --- data quality and versions ------------------------------------------------------------------


@pytest.mark.parametrize(("unsure", "limit", "status"), [(1, 0.1, "passed"), (2, 0.1, "failed")])
def test_uncertainty_rate_boundary(unsure: int, limit: float, status: str) -> None:
    result = gate(
        comparison(unsure, candidate_outcome=U),
        max_success_rate_drop=None,
        max_newly_failing_cases=None,
        max_evaluator_uncertainty_rate=limit,
    )
    assert result.status("evaluator_uncertainty") == status


def test_not_comparable_units_warn_by_default_and_fail_beyond_a_limit() -> None:
    gaps = compare(results("b", uniform(P, 4)), results("c", {**uniform(P, 3), TEN[3]: []}))
    assert gate(gaps).status("not_comparable_units") is RuleStatus.WARNING
    assert gate(gaps, max_not_comparable_fraction=0.25).status("not_comparable_units") == "passed"
    assert gate(gaps, max_not_comparable_fraction=0.2).status("not_comparable_units") == "failed"


def test_version_changes_fail_unless_allowed() -> None:
    from app.evaluation.engine import Evaluator
    from app.evaluation.judge import LLMJudge
    from app.experiments.samples import BASELINE_VERSION, sample_configuration
    from tests.evaluation.test_judge import FakeClient

    other_evaluator = sample_configuration(
        BASELINE_VERSION, scenario_ids=[TEN[0]], evaluator=Evaluator(LLMJudge(FakeClient([])))
    )
    changed = compare(
        results("b", {TEN[0]: [P]}), results("c", {TEN[0]: [P]}, configuration=other_evaluator)
    )
    assert changed.evaluator_changed
    assert gate(changed).status("evaluator_version") is RuleStatus.FAILED
    assert (
        gate(changed, allow_evaluator_change=True).status("evaluator_version") is RuleStatus.PASSED
    )


# --- performance and cost -----------------------------------------------------------------------


def _latency(base_seconds: float, cand_seconds: float) -> ExperimentComparison:
    outcomes = {TEN[0]: [P] * 20}
    return compare(
        results("b", outcomes, durations={TEN[0]: base_seconds}),
        results("c", outcomes, durations={TEN[0]: cand_seconds}),
    )


@pytest.mark.parametrize(("cand", "status"), [(11.0, "passed"), (11.5, "failed"), (9.0, "passed")])
def test_latency_increase_boundary(cand: float, status: str) -> None:
    assert (
        gate(_latency(10.0, cand), max_latency_p95_increase=0.1).status("latency_p95_increase")
        == status
    )


def test_latency_rule_with_too_few_samples_is_unavailable() -> None:
    small = compare(results("b", {TEN[0]: [P]}), results("c", {TEN[0]: [P]}))
    result = gate(small, max_latency_p95_increase=0.1)
    assert result.status("latency_p95_increase") is RuleStatus.UNAVAILABLE
    assert "p95 needs at least 20 runs" in next(
        r.message for r in result.rules if r.rule == "latency_p95_increase"
    )


def test_cost_rule_needs_complete_cost_data() -> None:
    result = gate(_latency(1.0, 1.0), max_cost_per_run_increase=0.1)
    assert result.status("cost_per_run_increase") is RuleStatus.UNAVAILABLE


def test_exit_codes() -> None:
    assert gate(comparison(0)).exit_code == 0
    assert gate(comparison(1)).exit_code == 1
