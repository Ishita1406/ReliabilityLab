import type { ExperimentMetrics } from "@/lib/api/types";
import { humanize, percent } from "@/lib/format";

import { EmptyState, Missing } from "./ui";

/**
 * Failure label rates as a labelled bar list. A chart library would add little here: each bar
 * is one proportion and the exact count is printed next to it.
 */
export function FailureCategories({ metrics }: { metrics: ExperimentMetrics }) {
  const entries = Object.entries(metrics.failure_rate);
  const anyAvailable = entries.some(([, m]) => m.value !== null);
  if (!anyAvailable) {
    const reason = entries[0]?.[1].unavailable_reason;
    return <Missing reason={reason ?? "no evaluated runs"} />;
  }
  const present = entries
    .filter(([, m]) => (m.numerator ?? 0) > 0)
    .sort(([, a], [, b]) => (b.value ?? 0) - (a.value ?? 0));
  if (present.length === 0) {
    return <EmptyState title="No failure labels in this experiment's evaluated runs" />;
  }
  return (
    <ul className="flex flex-col gap-2" aria-label="Failure categories">
      {present.map(([label, metric]) => (
        <li
          key={label}
          className="grid grid-cols-[minmax(8rem,14rem)_1fr_auto] items-center gap-3 text-sm"
        >
          <span>{humanize(label)}</span>
          <span aria-hidden="true" className="h-2 rounded bg-zinc-200 dark:bg-zinc-800">
            <span
              className="block h-2 rounded bg-red-600 dark:bg-red-400"
              style={{ width: `${(metric.value ?? 0) * 100}%` }}
            />
          </span>
          <span className="tabular-nums">
            {percent(metric.value ?? 0)} ({metric.numerator} / {metric.denominator} runs)
          </span>
        </li>
      ))}
    </ul>
  );
}
