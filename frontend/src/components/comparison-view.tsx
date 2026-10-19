"use client";

import { useMemo, useState } from "react";

import type { ComparisonResponse, MetricComparison, UnitComparison } from "@/lib/api/types";
import { humanize, metricText } from "@/lib/format";

import {
  Badge,
  EmptyState,
  Missing,
  ScenarioLabel,
  Section,
  StatusBadge,
  Table,
  td,
  th,
} from "./ui";

const STATUS_ORDER = ["newly_failing", "newly_passing", "not_comparable", "unchanged"] as const;

function sideText(side: UnitComparison["baseline"]): string {
  if (side === null) return "not planned";
  return `${side.outcome} (${side.passed}/${side.planned} passed${side.missing ? `, ${side.missing} missing` : ""})`;
}

function delta(item: MetricComparison): string {
  if (item.delta === null) return "";
  const ratio = item.baseline.unit === "ratio";
  const value = ratio ? `${(item.delta * 100).toFixed(1)} pp` : item.delta.toPrecision(3);
  return item.delta > 0 ? `+${value}` : value;
}

export function MetricRow({ item }: { item: MetricComparison }) {
  const base = metricText(item.baseline);
  const cand = metricText(item.candidate);
  return (
    <tr>
      <th scope="row" className={`${td} text-left font-medium`}>
        {humanize(item.name)}
      </th>
      <td className={`${td} tabular-nums`}>
        {base ?? <Missing reason={item.baseline.unavailable_reason} />}
      </td>
      <td className={`${td} tabular-nums`}>
        {cand ?? <Missing reason={item.candidate.unavailable_reason} />}
      </td>
      <td className={`${td} tabular-nums`}>
        {item.delta === null ? (
          <Missing reason="needs both sides" />
        ) : (
          <>
            {delta(item)}
            {item.delta_ci_low !== null && item.delta_ci_high !== null ? (
              <span className="block text-xs text-zinc-500">
                95% CI {(item.delta_ci_low * 100).toFixed(1)} to{" "}
                {(item.delta_ci_high * 100).toFixed(1)} pp
              </span>
            ) : null}
          </>
        )}
      </td>
      <td className={td}>
        {item.significant === null ? (
          <span className="text-zinc-500">no test</span>
        ) : item.significant ? (
          <Badge tone="warn">significant</Badge>
        ) : (
          <span>not significant</span>
        )}
      </td>
    </tr>
  );
}

export function ComparisonView({ data }: { data: ComparisonResponse }) {
  const { comparison, evaluation } = data;
  const [status, setStatus] = useState<string>("changed");
  const counts = useMemo(
    () =>
      Object.fromEntries(
        STATUS_ORDER.map((s) => [s, comparison.units.filter((u) => u.status === s).length]),
      ),
    [comparison.units],
  );
  const units = useMemo(() => {
    const rank = (u: UnitComparison) => STATUS_ORDER.indexOf(u.status);
    return comparison.units
      .filter((u) =>
        status === "all"
          ? true
          : status === "changed"
            ? u.status !== "unchanged"
            : u.status === status,
      )
      .sort((a, b) => rank(a) - rank(b) || a.scenario_id.localeCompare(b.scenario_id));
  }, [comparison.units, status]);
  const metrics = comparison.metrics.filter(
    (m) =>
      !m.name.startsWith("failure_rate.") ||
      (m.baseline.numerator ?? 0) + (m.candidate.numerator ?? 0) > 0,
  );
  const paired = comparison.paired_test;

  return (
    <>
      <div
        role="status"
        className={`mb-6 rounded-lg border p-4 ${
          evaluation.passed
            ? "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950"
            : "border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950"
        }`}
      >
        <p className="font-semibold">
          Regression policy {evaluation.passed ? "passed" : "failed"} (default policy)
        </p>
        <ul className="mt-2 flex flex-col gap-1 text-sm">
          {evaluation.rules
            .filter((rule) => rule.status !== "disabled")
            .map((rule) => (
              <li key={rule.rule} className="flex flex-wrap items-center gap-2">
                <StatusBadge status={rule.status} />
                <span className="font-medium">{humanize(rule.rule)}</span>
                {rule.hard ? <Badge tone="bad">security hard failure</Badge> : null}
                <span>{rule.message}</span>
              </li>
            ))}
        </ul>
      </div>

      <Section title="Configuration differences">
        {comparison.configuration_differences.length === 0 ? (
          <EmptyState title="Identical configurations" />
        ) : (
          <Table caption="Configuration differences">
            <thead>
              <tr>
                <th className={th}>Field</th>
                <th className={th}>Baseline</th>
                <th className={th}>Candidate</th>
              </tr>
            </thead>
            <tbody>
              {comparison.configuration_differences.map((difference) => (
                <tr key={difference.field}>
                  <th scope="row" className={`${td} text-left font-medium`}>
                    {humanize(difference.field)}
                  </th>
                  <td className={`${td} max-w-sm break-words`}>{difference.baseline}</td>
                  <td className={`${td} max-w-sm break-words`}>{difference.candidate}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>

      <Section
        title="Case-level changes"
        description={`Paired test: ${paired.newly_passing} newly passing vs ${paired.newly_failing} newly failing over ${paired.units} comparable cases, p = ${paired.p_value.toPrecision(3)} (${paired.significant ? "significant" : "not significant"} at α = ${paired.alpha}).`}
      >
        <fieldset className="mb-3 flex flex-wrap gap-3 text-sm">
          <legend className="sr-only">Show cases</legend>
          {[
            [
              "changed",
              `Changed or not comparable (${comparison.units.length - (counts.unchanged ?? 0)})`,
            ],
            ...STATUS_ORDER.map((s) => [s, `${humanize(s)} (${counts[s] ?? 0})`]),
            ["all", `All (${comparison.units.length})`],
          ].map(([value, label]) => (
            <label key={value} className="flex items-center gap-1">
              <input
                type="radio"
                name="unit-status"
                value={value}
                checked={status === value}
                onChange={() => setStatus(value!)}
              />
              {label}
            </label>
          ))}
        </fieldset>
        {units.length === 0 ? (
          <EmptyState title="No cases in this group" />
        ) : (
          <Table caption="Case-level comparison">
            <thead>
              <tr>
                <th className={th}>Scenario</th>
                <th className={th}>Change</th>
                <th className={th}>Baseline</th>
                <th className={th}>Candidate</th>
                <th className={th}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {units.map((unit) => (
                <tr key={unit.scenario_id}>
                  <td className={td}>
                    <ScenarioLabel scenarioId={unit.scenario_id} caseId={unit.case_id} />
                  </td>
                  <td className={td}>
                    <StatusBadge status={unit.status} />
                  </td>
                  <td className={td}>{sideText(unit.baseline)}</td>
                  <td className={td}>{sideText(unit.candidate)}</td>
                  <td className={td}>{unit.reason}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>

      <Section
        title="Metrics side by side"
        description="Intervals for rate differences treat runs as independent; repeated runs of one case are not, so they can be too narrow."
      >
        <Table caption="Metrics comparison">
          <thead>
            <tr>
              <th className={th}>Metric</th>
              <th className={th}>Baseline</th>
              <th className={th}>Candidate</th>
              <th className={th}>Change</th>
              <th className={th}>Test</th>
            </tr>
          </thead>
          <tbody>
            <MetricRow item={comparison.matched_success} />
            {metrics.map((item) => (
              <MetricRow key={item.name} item={item} />
            ))}
          </tbody>
        </Table>
      </Section>
    </>
  );
}
