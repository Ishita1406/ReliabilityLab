"""Metric definitions, sample sizes, and explicit unavailability."""

import pytest

from app.agents.interface import ModelUsage
from app.evaluation.models import FailureLabel
from app.experiments.metrics import MetricValue, compute_metrics
from app.experiments.pricing import ModelPrice, PricingTable, run_cost
from app.experiments.samples import BASELINE_VERSION, run_sample_experiment, sample_configuration
from tests.experiments.helpers import SCENARIOS, F, P, U, results

GROQ_USAGE = [ModelUsage(provider="groq", model="m", input_tokens=1_000_000, output_tokens=500_000)]
PRICES = PricingTable(
    prices=[
        ModelPrice(
            provider="groq",
            model="m",
            input_usd_per_million_tokens=0.5,
            output_usd_per_million_tokens=2.0,
        )
    ],
    source="test fixture (not real prices)",
)


def test_rates_carry_sample_sizes_and_intervals() -> None:
    metrics = compute_metrics(
        results("rates", {SCENARIOS[0]: [P, P], SCENARIOS[1]: [F, U], SCENARIOS[2]: [P, P]})
    )
    success = metrics.task_success_rate
    assert (success.numerator, success.denominator, success.sample_unit) == (4, 6, "runs")
    assert success.value == pytest.approx(4 / 6)
    assert success.ci_low is not None and success.ci_high is not None
    assert success.ci_low < 4 / 6 < success.ci_high
    assert metrics.evaluator_uncertainty_rate.numerator == 1
    assert metrics.failure_rate[FailureLabel.TASK_CORRECTNESS].numerator == 1
    assert metrics.failure_rate[FailureLabel.EVALUATOR_UNCERTAINTY].numerator == 1
    assert (metrics.planned_runs, metrics.evaluated_runs, metrics.missing_runs) == (6, 6, 0)


def test_missing_runs_and_unevaluated_runs_are_counted_not_hidden() -> None:
    metrics = compute_metrics(results("gaps", {SCENARIOS[0]: [P, None], SCENARIOS[1]: [P]}))
    assert (metrics.planned_runs, metrics.observed_runs, metrics.evaluated_runs) == (4, 3, 2)
    assert metrics.missing_runs == 2
    assert metrics.task_success_rate.denominator == 2


def test_unmeasured_metrics_are_unavailable_not_zero() -> None:
    metrics = compute_metrics(results("plain", {SCENARIOS[0]: [P]}))
    for metric in (
        metrics.unauthorized_action_rate,
        metrics.fault_recovery_rate,
        metrics.tool_failure_recovery_rate,
        metrics.cost_total,
        metrics.cost_per_run_mean,
        metrics.latency_p90,
        metrics.latency_p95,
    ):
        assert metric.value is None and metric.unavailable_reason
    assert "not measured" in (metrics.unauthorized_action_rate.unavailable_reason or "")
    assert metrics.cost_total.unavailable_reason == (
        "cost unknown for 1 of 1 run(s) (1 run(s): usage not reported)"
    )


def test_unauthorized_rate_counts_only_runs_where_it_was_measured() -> None:
    metrics = compute_metrics(
        results(
            "auth",
            {SCENARIOS[0]: [P], SCENARIOS[1]: [F], SCENARIOS[2]: [P]},
            unauthorized={SCENARIOS[0]: False, SCENARIOS[1]: True},
        )
    )
    rate = metrics.unauthorized_action_rate
    assert (rate.numerator, rate.denominator) == (1, 2)


@pytest.mark.parametrize(
    ("runs", "p90", "p95"), [(9, False, False), (10, True, False), (20, True, True)]
)
def test_percentiles_need_enough_samples(runs: int, p90: bool, p95: bool) -> None:
    metrics = compute_metrics(results("lat", {s: [P] * runs for s in SCENARIOS[:1]}))
    assert metrics.latency_p50.available
    assert metrics.latency_p90.available is p90
    assert metrics.latency_p95.available is p95
    assert metrics.latency_mean.denominator == runs


def test_latency_uses_run_durations() -> None:
    metrics = compute_metrics(
        results("lat2", {SCENARIOS[0]: [P], SCENARIOS[1]: [P]}, durations={SCENARIOS[1]: 3.0})
    )
    assert metrics.latency_mean.value == pytest.approx(2.0)
    assert metrics.latency_p50.value == pytest.approx(2.0)


def test_cost_is_known_only_with_usage_and_prices() -> None:
    assert run_cost(None, PRICES) == (None, "usage not reported")
    assert run_cost([], PricingTable()) == (0.0, "no model calls")
    assert run_cost(GROQ_USAGE, PricingTable()) == (None, "no price for groq/m")
    cost, reason = run_cost(GROQ_USAGE, PRICES)
    assert (cost, reason) == (1.5, "priced")  # 1M in x $0.5 + 0.5M out x $2.0

    priced = compute_metrics(results("cost", {SCENARIOS[0]: [P, P]}, usage=GROQ_USAGE), PRICES)
    assert priced.cost_total.value == pytest.approx(3.0)
    assert priced.cost_per_run_mean.value == pytest.approx(1.5)
    unpriced = compute_metrics(results("cost2", {SCENARIOS[0]: [P]}, usage=GROQ_USAGE))
    assert unpriced.cost_total.value is None
    assert "no price for groq/m" in (unpriced.cost_total.unavailable_reason or "")


def test_partially_known_cost_reports_coverage_and_no_total() -> None:
    known = results("part", {SCENARIOS[0]: [P]}, usage=GROQ_USAGE)
    unknown = results("part", {SCENARIOS[1]: [P]})
    merged = known.model_copy(update={"configuration": None, "runs": known.runs + unknown.runs})
    metrics = compute_metrics(merged, PRICES)
    assert metrics.cost_per_run_mean.value == pytest.approx(1.5)
    assert (metrics.cost_per_run_mean.numerator, metrics.cost_per_run_mean.denominator) == (1, 2)
    assert metrics.cost_total.value is None
    assert metrics.planned_runs is None and metrics.missing_runs is None


def test_deterministic_agent_reports_known_zero_usage() -> None:
    real = run_sample_experiment("cost", sample_configuration(BASELINE_VERSION))
    metrics = compute_metrics(real)
    assert metrics.cost_total.value == 0.0  # known: the agent reported no model calls
    assert metrics.cost_total.denominator == len(real.runs)


def test_metric_value_requires_a_value_or_a_reason() -> None:
    with pytest.raises(ValueError):
        MetricValue(value=None, unit="ratio")
    with pytest.raises(ValueError):
        MetricValue(value=0.5, unit="ratio", unavailable_reason="both")
