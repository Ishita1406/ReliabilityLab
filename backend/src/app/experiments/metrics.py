"""Experiment-level metrics.

Every metric is a ``MetricValue``: either a value with its sample size (``denominator``) and,
for proportions, a 95% Wilson interval, or an explicit ``unavailable_reason``. Nothing that
could not be measured is reported as zero.

Definitions (n = runs with an evaluation):

==============================  ========================================================  =======
metric                          definition                                                unit
==============================  ========================================================  =======
task_success_rate               runs with verdict ``passed`` / n                          runs
failure_rate[label]             runs carrying failure label ``label`` / n                 runs
evaluator_uncertainty_rate      runs with verdict ``inconclusive`` / n                    runs
fault_recovery_rate             passed / decided ``expected_recovery`` criteria           criteria
tool_failure_recovery_rate      fault-caused failed tool calls with a later successful    calls
                                call to the same tool / fault-caused failed tool calls
unauthorized_action_rate        runs whose ``prohibited_actions`` criterion failed /      runs
                                runs where such a criterion was decided
latency_mean / p50 / p90 / p95  run duration on the run clock (all runs with timestamps)  seconds
cost_per_run_mean               mean cost over runs whose cost is known                   USD
cost_total                      sum of run costs; only if every run's cost is known       USD
==============================  ========================================================  =======

Percentiles need enough samples to mean anything: p90 needs at least 10 runs and p95 at least
20; below that they are unavailable (with fewer samples they are just the maximum).
"""

from collections.abc import Callable
from typing import Literal, Self

from pydantic import Field, NonNegativeInt, model_validator

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.enums import CriterionType
from app.evaluation.models import CaseEvaluation, CriterionStatus, FailureLabel, Verdict
from app.experiments.pricing import PricingTable, run_cost
from app.experiments.records import ExperimentResults
from app.experiments.stats import percentile, wilson_interval

MIN_SAMPLES_FOR_PERCENTILE = {50: 1, 90: 10, 95: 20}

SampleUnit = Literal["runs", "criteria", "tool_calls"]


class MetricValue(DomainModel):
    value: float | None
    unit: Literal["ratio", "seconds", "usd"]
    sample_unit: SampleUnit = "runs"
    numerator: NonNegativeInt | None = None
    denominator: NonNegativeInt | None = Field(default=None, description="Sample size.")
    ci_low: float | None = None
    ci_high: float | None = None
    unavailable_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.value is None) == (self.unavailable_reason is None):
            raise ValueError("a metric has either a value or an unavailable_reason")
        if (self.ci_low is None) != (self.ci_high is None):
            raise ValueError("confidence bounds come in pairs")
        return self

    @property
    def available(self) -> bool:
        return self.value is not None

    @classmethod
    def proportion(
        cls, numerator: int, denominator: int, *, sample_unit: SampleUnit, if_empty: str
    ) -> Self:
        if denominator == 0:
            return cls.unavailable(if_empty, unit="ratio", sample_unit=sample_unit, sample_size=0)
        low, high = wilson_interval(numerator, denominator)
        return cls(
            value=numerator / denominator,
            unit="ratio",
            sample_unit=sample_unit,
            numerator=numerator,
            denominator=denominator,
            ci_low=low,
            ci_high=high,
        )

    @classmethod
    def unavailable(
        cls,
        reason: str,
        *,
        unit: Literal["ratio", "seconds", "usd"],
        sample_unit: SampleUnit = "runs",
        sample_size: int | None = None,
    ) -> Self:
        return cls(
            value=None,
            unit=unit,
            sample_unit=sample_unit,
            denominator=sample_size,
            unavailable_reason=reason,
        )


class ExperimentMetrics(DomainModel):
    experiment_id: NonEmptyStr
    planned_runs: NonNegativeInt | None
    observed_runs: NonNegativeInt
    evaluated_runs: NonNegativeInt
    missing_runs: NonNegativeInt | None = Field(description="planned - evaluated; null if unknown")
    task_success_rate: MetricValue
    failure_rate: dict[FailureLabel, MetricValue]
    evaluator_uncertainty_rate: MetricValue
    fault_recovery_rate: MetricValue
    tool_failure_recovery_rate: MetricValue
    unauthorized_action_rate: MetricValue
    latency_mean: MetricValue
    latency_p50: MetricValue
    latency_p90: MetricValue
    latency_p95: MetricValue
    cost_per_run_mean: MetricValue
    cost_total: MetricValue

    def scalar(self, name: str) -> MetricValue:
        value = getattr(self, name)
        if not isinstance(value, MetricValue):
            raise KeyError(name)
        return value


NO_EVALUATIONS = "no evaluated runs"


def compute_metrics(
    results: ExperimentResults, pricing: PricingTable | None = None
) -> ExperimentMetrics:
    evaluated = [r.evaluation for r in results.runs if r.evaluation is not None]
    n = len(evaluated)

    def runs_where(predicate: Callable[[CaseEvaluation], bool]) -> int:
        return sum(1 for e in evaluated if predicate(e))

    def labelled(label: FailureLabel) -> Callable[[CaseEvaluation], bool]:
        return lambda evaluation: label in evaluation.labels

    failure_rate = {
        label: MetricValue.proportion(
            runs_where(labelled(label)),
            n,
            sample_unit="runs",
            if_empty=NO_EVALUATIONS,
        )
        for label in FailureLabel
    }

    recovery = [
        c
        for e in evaluated
        for c in e.criteria
        if c.criterion_type is CriterionType.EXPECTED_RECOVERY
        and c.status is not CriterionStatus.INCONCLUSIVE
    ]
    faulted_failures = [
        f for r in results.runs if r.evaluation for f in r.evaluation.tool_failures if f.fault_ids
    ]
    prohibited = [
        any(
            c.criterion_type is CriterionType.PROHIBITED_ACTIONS
            and c.status is CriterionStatus.FAILED
            for c in e.criteria
        )
        for e in evaluated
        if any(
            c.criterion_type is CriterionType.PROHIBITED_ACTIONS
            and c.status is not CriterionStatus.INCONCLUSIVE
            for c in e.criteria
        )
    ]

    durations = [r.duration_seconds for r in results.runs if r.duration_seconds is not None]

    def latency(q: int | None) -> MetricValue:
        if q is None:
            if not durations:
                return MetricValue.unavailable("no run durations", unit="seconds", sample_size=0)
            return MetricValue(
                value=sum(durations) / len(durations), unit="seconds", denominator=len(durations)
            )
        needed = MIN_SAMPLES_FOR_PERCENTILE[q]
        if len(durations) < needed:
            return MetricValue.unavailable(
                f"p{q} needs at least {needed} runs, have {len(durations)}",
                unit="seconds",
                sample_size=len(durations),
            )
        return MetricValue(
            value=percentile(durations, q), unit="seconds", denominator=len(durations)
        )

    costs: list[float] = []
    unknown_reasons: dict[str, int] = {}
    for record in results.runs:
        cost, reason = run_cost(record.usage, pricing or PricingTable())
        if cost is None:
            unknown_reasons[reason] = unknown_reasons.get(reason, 0) + 1
        else:
            costs.append(cost)
    observed = len(results.runs)
    why_unknown = ", ".join(
        f"{count} run(s): {reason}" for reason, count in sorted(unknown_reasons.items())
    )
    if costs:
        cost_mean = MetricValue(
            value=sum(costs) / len(costs), unit="usd", numerator=len(costs), denominator=observed
        )
    else:
        cost_mean = MetricValue.unavailable(
            f"cost unknown for every run ({why_unknown or 'no runs'})",
            unit="usd",
            sample_size=observed,
        )
    if costs and len(costs) == observed:
        cost_total = MetricValue(
            value=sum(costs), unit="usd", numerator=observed, denominator=observed
        )
    else:
        cost_total = MetricValue.unavailable(
            f"cost unknown for {observed - len(costs)} of {observed} run(s)"
            + (f" ({why_unknown})" if why_unknown else ""),
            unit="usd",
            sample_size=observed,
        )

    planned = results.planned_runs
    return ExperimentMetrics(
        experiment_id=results.experiment_id,
        planned_runs=planned,
        observed_runs=observed,
        evaluated_runs=n,
        missing_runs=None if planned is None else planned - n,
        task_success_rate=MetricValue.proportion(
            runs_where(lambda e: e.verdict is Verdict.PASSED),
            n,
            sample_unit="runs",
            if_empty=NO_EVALUATIONS,
        ),
        failure_rate=failure_rate,
        evaluator_uncertainty_rate=MetricValue.proportion(
            runs_where(lambda e: e.verdict is Verdict.INCONCLUSIVE),
            n,
            sample_unit="runs",
            if_empty=NO_EVALUATIONS,
        ),
        fault_recovery_rate=MetricValue.proportion(
            sum(1 for c in recovery if c.status is CriterionStatus.PASSED),
            len(recovery),
            sample_unit="criteria",
            if_empty="no decided expected_recovery criteria (none defined, or no fault fired)",
        ),
        tool_failure_recovery_rate=MetricValue.proportion(
            sum(1 for f in faulted_failures if f.recovered),
            len(faulted_failures),
            sample_unit="tool_calls",
            if_empty="no tool call failed because of an injected fault",
        ),
        unauthorized_action_rate=MetricValue.proportion(
            sum(prohibited),
            len(prohibited),
            sample_unit="runs",
            if_empty="no run has a decided prohibited_actions criterion; not measured",
        ),
        latency_mean=latency(None),
        latency_p50=latency(50),
        latency_p90=latency(90),
        latency_p95=latency(95),
        cost_per_run_mean=cost_mean,
        cost_total=cost_total,
    )
