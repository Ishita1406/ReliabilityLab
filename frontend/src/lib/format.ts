/**
 * Formatting helpers. They never turn a missing value into zero: missing values return `null`
 * and the `<Missing>` component renders them with their reason.
 */
import type { MetricValue } from "./api/types";

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function seconds(value: number): string {
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value.toFixed(value < 10 ? 2 : 1)} s`;
  return `${Math.floor(value / 60)} min ${Math.round(value % 60)} s`;
}

export function usd(value: number): string {
  return value === 0 ? "$0" : value < 0.01 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`;
}

/** Formatted metric value, or null when the metric is unavailable. */
export function metricText(metric: MetricValue): string | null {
  if (metric.value === null) return null;
  switch (metric.unit) {
    case "ratio":
      return percent(metric.value);
    case "seconds":
      return seconds(metric.value);
    case "usd":
      return usd(metric.value);
  }
}

/** "11 / 12 runs, 95% CI 64.6–98.5%" style detail for proportions; sample size otherwise. */
export function metricDetail(metric: MetricValue): string | null {
  if (metric.value === null) return null;
  const unit = metric.sample_unit.replace("_", " ");
  if (metric.unit === "ratio" && metric.numerator !== null && metric.denominator !== null) {
    const ci =
      metric.ci_low !== null && metric.ci_high !== null
        ? `, 95% CI ${percent(metric.ci_low)}–${percent(metric.ci_high)}`
        : "";
    return `${metric.numerator} / ${metric.denominator} ${unit}${ci}`;
  }
  if (metric.unit === "usd" && metric.numerator !== null && metric.denominator !== null) {
    return `known for ${metric.numerator} of ${metric.denominator} runs`;
  }
  return metric.denominator !== null ? `n = ${metric.denominator}` : null;
}

export function dateTime(value: string | null): string | null {
  if (value === null) return null;
  return new Date(value).toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

export function shortId(id: string): string {
  const [prefix, rest] = id.split("_");
  return rest ? `${prefix}_${rest.slice(0, 8)}` : id;
}

export function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

/** Last dotted segment of a scenario slug ("runner.lighthouse-year.baseline" -> "baseline"). */
export function scenarioName(scenarioId: string, caseId?: string): string {
  if (caseId && scenarioId.startsWith(`${caseId}.`)) return scenarioId.slice(caseId.length + 1);
  return scenarioId;
}
