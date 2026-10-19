"""Plain-text rendering of a comparison and its policy evaluation (for CLIs and CI logs)."""

from app.experiments.comparison import ExperimentComparison, MetricComparison, UnitStatus
from app.experiments.metrics import MetricValue
from app.experiments.policy import PolicyEvaluation


def format_metric(metric: MetricValue) -> str:
    if metric.value is None:
        return f"unavailable ({metric.unavailable_reason})"
    if metric.unit == "ratio":
        text = f"{metric.value:.1%}"
        if metric.numerator is not None:
            text += f" ({metric.numerator}/{metric.denominator} {metric.sample_unit}"
            if metric.ci_low is not None and metric.ci_high is not None:
                text += f", 95% CI {metric.ci_low:.1%}-{metric.ci_high:.1%}"
            text += ")"
        return text
    suffix = " s" if metric.unit == "seconds" else " USD"
    coverage = (
        f", known for {metric.numerator}/{metric.denominator} runs"
        if metric.unit == "usd" and metric.numerator is not None
        else f", n={metric.denominator}"
    )
    return f"{metric.value:.4g}{suffix}{coverage}"


def _metric_line(item: MetricComparison) -> str:
    line = f"  {item.name:34} {format_metric(item.baseline)}  ->  {format_metric(item.candidate)}"
    if item.delta is not None:
        line += f"  [delta {item.delta:+.4g}"
        if item.delta_ci_low is not None and item.delta_ci_high is not None:
            line += f", CI {item.delta_ci_low:+.3f}..{item.delta_ci_high:+.3f}"
        if item.significant is not None:
            line += ", significant" if item.significant else ", not significant"
        line += "]"
    return line


def render(comparison: ExperimentComparison, policy: PolicyEvaluation | None = None) -> str:
    lines = [f"baseline {comparison.baseline_id}  vs  candidate {comparison.candidate_id}"]
    if comparison.configuration_differences:
        lines.append("configuration differences:")
        for diff in comparison.configuration_differences:
            lines.append(f"  {diff.field}: {diff.baseline[:60]!r} -> {diff.candidate[:60]!r}")
    else:
        lines.append("configuration differences: none")
    lines.append("cases:")
    for status in UnitStatus:
        units = comparison.by_status(status)
        lines.append(f"  {status}: {len(units)}")
        if status is not UnitStatus.UNCHANGED:
            for unit in units:
                lines.append(f"    - {unit.scenario_id}: {unit.reason}")
    paired = comparison.paired_test
    lines.append(
        f"paired test: {paired.newly_passing} newly passing / {paired.newly_failing} newly "
        f"failing over {paired.units} comparable unit(s), p={paired.p_value:.3g} "
        + ("(significant)" if paired.significant else "(not significant)")
    )
    lines.append("metrics (baseline -> candidate):")
    lines.append(_metric_line(comparison.matched_success))
    for item in comparison.metrics:
        if (
            item.name.startswith("failure_rate.")
            and not item.baseline.numerator
            and not (item.candidate.numerator)
        ):
            continue  # omit labels that never occurred on either side
        lines.append(_metric_line(item))
    if policy is not None:
        lines.append("policy: " + ("PASSED" if policy.passed else "FAILED"))
        for rule in policy.rules:
            hard = " [security hard failure]" if rule.hard else ""
            lines.append(f"  {rule.status.upper():11} {rule.rule}{hard}: {rule.message}")
    return "\n".join(lines)
