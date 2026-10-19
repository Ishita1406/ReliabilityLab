"""Compare a candidate experiment against a baseline.

Unit of comparison: one scenario of one case (``case_id``, ``scenario_id``), because a case
under different faults is a different test. Each side's unit outcome over its planned
repetitions is:

* ``missing``      - fewer evaluated runs than planned (runs or evaluations absent);
* ``fail``         - at least one repetition failed (a unit passes only if all of them pass);
* ``inconclusive`` - no repetition failed, but at least one was inconclusive;
* ``pass``         - every planned repetition passed.

Unit status:

* ``not_comparable`` - the unit is planned on only one side, its definition (case + scenario
  fingerprint) differs between the two suites, or either side is missing or inconclusive. The
  reason says which.
* ``newly_passing``  - fail -> pass.
* ``newly_failing``  - pass -> fail.
* ``unchanged``      - pass -> pass or fail -> fail.

The matched success comparison and the paired test use only the comparable units, so missing
data or changed cases cannot masquerade as a regression or an improvement.
"""

from enum import StrEnum
from fractions import Fraction

from pydantic import Field, NonNegativeInt

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.ids import Slug
from app.evaluation.models import Verdict
from app.experiments.configuration import ConfigurationDifference, ExperimentConfiguration
from app.experiments.metrics import ExperimentMetrics, MetricValue, compute_metrics
from app.experiments.pricing import PricingTable
from app.experiments.records import ExperimentResults
from app.experiments.stats import mcnemar_exact_p, newcombe_difference

DEFAULT_ALPHA = 0.05


class ComparisonError(ValueError):
    """The experiments cannot be compared at all."""


class UnitOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    MISSING = "missing"


class UnitStatus(StrEnum):
    NEWLY_PASSING = "newly_passing"
    NEWLY_FAILING = "newly_failing"
    UNCHANGED = "unchanged"
    NOT_COMPARABLE = "not_comparable"


class UnitSide(DomainModel):
    outcome: UnitOutcome
    planned: NonNegativeInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    inconclusive: NonNegativeInt
    missing: NonNegativeInt


class UnitComparison(DomainModel):
    case_id: Slug
    scenario_id: Slug
    status: UnitStatus
    baseline: UnitSide | None
    candidate: UnitSide | None
    reason: NonEmptyStr


class PairedTest(DomainModel):
    """Exact McNemar test on comparable units (pass/fail per unit on both sides)."""

    method: str = "exact McNemar (two-sided), units as pairs"
    units: NonNegativeInt
    newly_passing: NonNegativeInt
    newly_failing: NonNegativeInt
    p_value: float = Field(ge=0, le=1)
    alpha: float
    significant: bool


class MetricComparison(DomainModel):
    name: NonEmptyStr
    baseline: MetricValue
    candidate: MetricValue
    delta: float | None = Field(description="candidate - baseline; null if either unavailable")
    delta_ci_low: float | None = None
    delta_ci_high: float | None = None
    significant: bool | None = Field(
        default=None, description="null: no test applies (e.g. latency, cost)."
    )
    note: str | None = None


class ExperimentComparison(DomainModel):
    baseline_id: NonEmptyStr
    candidate_id: NonEmptyStr
    baseline_fingerprint: str
    candidate_fingerprint: str
    configuration_differences: list[ConfigurationDifference]
    suite_changed: bool
    evaluator_changed: bool
    units: list[UnitComparison]
    matched_success: MetricComparison = Field(
        description="Run-level success over comparable units only."
    )
    matched_success_runs: tuple[tuple[int, int], tuple[int, int]] = Field(
        description="((baseline passed, runs), (candidate passed, runs)) for comparable units."
    )
    paired_test: PairedTest
    metrics: list[MetricComparison]
    baseline_metrics: ExperimentMetrics
    candidate_metrics: ExperimentMetrics

    def by_status(self, status: UnitStatus) -> list[UnitComparison]:
        return [u for u in self.units if u.status is status]

    def matched_success_drop(self) -> Fraction | None:
        """Exact baseline - candidate success rate on comparable units (None if none)."""
        (b_pass, b_n), (c_pass, c_n) = self.matched_success_runs
        if b_n == 0 or c_n == 0:
            return None
        return Fraction(b_pass, b_n) - Fraction(c_pass, c_n)


def _side(results: ExperimentResults, scenario_id: str) -> UnitSide:
    config = results.configuration
    if config is None:  # checked by compare()
        raise ComparisonError("missing configuration")
    records = [r for r in results.for_scenario(scenario_id) if r.evaluation is not None]
    verdicts = [r.evaluation.verdict for r in records if r.evaluation is not None]
    passed = verdicts.count(Verdict.PASSED)
    failed = verdicts.count(Verdict.FAILED)
    inconclusive = verdicts.count(Verdict.INCONCLUSIVE)
    missing = config.repetitions - len(verdicts)
    if missing > 0:
        outcome = UnitOutcome.MISSING
    elif failed:
        outcome = UnitOutcome.FAIL
    elif inconclusive:
        outcome = UnitOutcome.INCONCLUSIVE
    else:
        outcome = UnitOutcome.PASS
    return UnitSide(
        outcome=outcome,
        planned=config.repetitions,
        passed=passed,
        failed=failed,
        inconclusive=inconclusive,
        missing=missing,
    )


def _classify(
    scenario_id: str,
    baseline: ExperimentResults,
    candidate: ExperimentResults,
    base_config: ExperimentConfiguration,
    cand_config: ExperimentConfiguration,
) -> UnitComparison:
    case_id = (base_config.scenario_case_ids | cand_config.scenario_case_ids)[scenario_id]
    in_base = scenario_id in base_config.scenario_fingerprints
    in_cand = scenario_id in cand_config.scenario_fingerprints
    base = _side(baseline, scenario_id) if in_base else None
    cand = _side(candidate, scenario_id) if in_cand else None

    def unit(status: UnitStatus, reason: str) -> UnitComparison:
        return UnitComparison(
            case_id=case_id,
            scenario_id=scenario_id,
            status=status,
            baseline=base,
            candidate=cand,
            reason=reason,
        )

    if base is None or cand is None:
        side = "baseline" if cand is None else "candidate"
        return unit(UnitStatus.NOT_COMPARABLE, f"only planned in the {side}")
    if (
        base_config.scenario_fingerprints[scenario_id]
        != cand_config.scenario_fingerprints[scenario_id]
    ):
        return unit(
            UnitStatus.NOT_COMPARABLE,
            "case or scenario definition differs between the two benchmark versions",
        )
    problems = [
        f"{name}: {side.missing} of {side.planned} planned runs missing"
        for name, side in (("baseline", base), ("candidate", cand))
        if side.outcome is UnitOutcome.MISSING
    ] + [
        f"{name}: inconclusive evaluation ({side.inconclusive} run(s))"
        for name, side in (("baseline", base), ("candidate", cand))
        if side.outcome is UnitOutcome.INCONCLUSIVE
    ]
    if problems:
        return unit(UnitStatus.NOT_COMPARABLE, "; ".join(problems))
    transition = f"{base.outcome} -> {cand.outcome}"
    if base.outcome is cand.outcome:
        return unit(UnitStatus.UNCHANGED, transition)
    if cand.outcome is UnitOutcome.PASS:
        return unit(UnitStatus.NEWLY_PASSING, transition)
    return unit(UnitStatus.NEWLY_FAILING, transition)


def _metric_comparison(
    name: str, baseline: MetricValue, candidate: MetricValue
) -> MetricComparison:
    if baseline.value is None or candidate.value is None:
        missing = [
            f"{side}: {m.unavailable_reason}"
            for side, m in (("baseline", baseline), ("candidate", candidate))
            if m.value is None
        ]
        return MetricComparison(
            name=name, baseline=baseline, candidate=candidate, delta=None, note="; ".join(missing)
        )
    delta = candidate.value - baseline.value
    if (
        baseline.unit == "ratio"
        and baseline.numerator is not None
        and baseline.denominator
        and candidate.numerator is not None
        and candidate.denominator
    ):
        low, high = newcombe_difference(
            baseline.numerator, baseline.denominator, candidate.numerator, candidate.denominator
        )
        return MetricComparison(
            name=name,
            baseline=baseline,
            candidate=candidate,
            delta=delta,
            delta_ci_low=low,
            delta_ci_high=high,
            significant=low > 0 or high < 0,
            note=f"Newcombe 95% interval; treats {baseline.sample_unit} as independent",
        )
    return MetricComparison(
        name=name,
        baseline=baseline,
        candidate=candidate,
        delta=delta,
        note="no significance test for this metric",
    )


SCALAR_METRICS = (
    "task_success_rate",
    "evaluator_uncertainty_rate",
    "fault_recovery_rate",
    "tool_failure_recovery_rate",
    "unauthorized_action_rate",
    "latency_mean",
    "latency_p50",
    "latency_p90",
    "latency_p95",
    "cost_per_run_mean",
    "cost_total",
)


def compare(
    baseline: ExperimentResults,
    candidate: ExperimentResults,
    *,
    pricing: PricingTable | None = None,
    alpha: float = DEFAULT_ALPHA,
) -> ExperimentComparison:
    for name, results in (("baseline", baseline), ("candidate", candidate)):
        if results.configuration is None:
            raise ComparisonError(
                f"{name} experiment '{results.experiment_id}' has no configuration snapshot "
                "(created before versioned experiments); it cannot be compared"
            )
    base_config, cand_config = baseline.configuration, candidate.configuration
    assert base_config is not None and cand_config is not None  # narrowed above

    scenario_ids = list(dict.fromkeys([*base_config.scenario_ids, *cand_config.scenario_ids]))
    units = [_classify(s, baseline, candidate, base_config, cand_config) for s in scenario_ids]

    comparable = [u for u in units if u.status is not UnitStatus.NOT_COMPARABLE]
    b_pass = sum(u.baseline.passed for u in comparable if u.baseline)
    b_runs = sum(u.baseline.planned for u in comparable if u.baseline)
    c_pass = sum(u.candidate.passed for u in comparable if u.candidate)
    c_runs = sum(u.candidate.planned for u in comparable if u.candidate)
    empty = "no comparable units"
    matched = _metric_comparison(
        "matched_task_success_rate",
        MetricValue.proportion(b_pass, b_runs, sample_unit="runs", if_empty=empty),
        MetricValue.proportion(c_pass, c_runs, sample_unit="runs", if_empty=empty),
    )

    newly_passing = sum(1 for u in units if u.status is UnitStatus.NEWLY_PASSING)
    newly_failing = sum(1 for u in units if u.status is UnitStatus.NEWLY_FAILING)
    p_value = mcnemar_exact_p(newly_passing, newly_failing)
    paired = PairedTest(
        units=len(comparable),
        newly_passing=newly_passing,
        newly_failing=newly_failing,
        p_value=p_value,
        alpha=alpha,
        significant=p_value < alpha,
    )

    base_metrics = compute_metrics(baseline, pricing)
    cand_metrics = compute_metrics(candidate, pricing)
    metrics = [
        _metric_comparison(name, base_metrics.scalar(name), cand_metrics.scalar(name))
        for name in SCALAR_METRICS
    ] + [
        _metric_comparison(
            f"failure_rate.{label}",
            base_metrics.failure_rate[label],
            cand_metrics.failure_rate[label],
        )
        for label in base_metrics.failure_rate
    ]
    return ExperimentComparison(
        baseline_id=baseline.experiment_id,
        candidate_id=candidate.experiment_id,
        baseline_fingerprint=base_config.fingerprint,
        candidate_fingerprint=cand_config.fingerprint,
        configuration_differences=base_config.differences(cand_config),
        suite_changed=(base_config.suite_id, base_config.suite_version, base_config.suite_sha256)
        != (cand_config.suite_id, cand_config.suite_version, cand_config.suite_sha256),
        evaluator_changed=base_config.evaluator_version != cand_config.evaluator_version,
        units=units,
        matched_success=matched,
        matched_success_runs=((b_pass, b_runs), (c_pass, c_runs)),
        paired_test=paired,
        metrics=metrics,
        baseline_metrics=base_metrics,
        candidate_metrics=cand_metrics,
    )
