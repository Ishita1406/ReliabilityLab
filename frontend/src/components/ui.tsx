/**
 * Shared presentational components. Status is always conveyed by text (never colour alone),
 * and missing values are rendered as "Not available" with a reason, never as zero.
 */
import Link from "next/link";
import { type ReactNode, useId } from "react";

import type { ApiError } from "@/lib/api/client";
import type { MetricValue } from "@/lib/api/types";
import { humanize, metricDetail, metricText, scenarioName } from "@/lib/format";

export function PageHeader({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-2 border-b border-zinc-200 pb-4 dark:border-zinc-800">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {description ? (
        <p className="max-w-3xl text-sm leading-6 text-zinc-600 dark:text-zinc-400">
          {description}
        </p>
      ) : null}
      {children}
    </header>
  );
}

export function Section({
  title,
  description,
  children,
  id,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  // Every section is a named region (announced by screen readers, addressable by tests).
  const generated = useId();
  const headingId = `${id ?? generated}-heading`;
  return (
    <section aria-labelledby={headingId} id={id} className="mb-8">
      <h2 id={headingId} className="mb-1 text-lg font-semibold">
        {title}
      </h2>
      {description ? (
        <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">{description}</p>
      ) : null}
      <div className={description ? "" : "mt-3"}>{children}</div>
    </section>
  );
}

export function Missing({ reason }: { reason?: string | null }) {
  return (
    <span className="text-zinc-500 italic dark:text-zinc-400">
      Not available{reason ? <span className="not-italic">: {reason}</span> : null}
    </span>
  );
}

export function MetricTile({ label, metric }: { label: string; metric: MetricValue }) {
  const text = metricText(metric);
  const detail = metricDetail(metric);
  return (
    <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <dt className="text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
        {label}
      </dt>
      <dd className="mt-1">
        {text === null ? (
          <span className="text-sm">
            <Missing reason={metric.unavailable_reason} />
          </span>
        ) : (
          <>
            <span className="text-2xl font-semibold tabular-nums">{text}</span>
            {detail ? (
              <span className="mt-1 block text-xs text-zinc-500 dark:text-zinc-400">{detail}</span>
            ) : null}
          </>
        )}
      </dd>
    </div>
  );
}

const TONES = {
  good: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  bad: "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200",
  warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
  neutral:
    "border-zinc-300 bg-zinc-50 text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200",
} as const;

export type Tone = keyof typeof TONES;

const STATUS_TONES: Record<string, Tone> = {
  passed: "good",
  completed: "good",
  ok: "good",
  newly_passing: "good",
  failed: "bad",
  errored: "bad",
  error: "bad",
  timeout: "bad",
  timed_out: "bad",
  limit_exceeded: "bad",
  newly_failing: "bad",
  rejected: "bad",
  inconclusive: "warn",
  warning: "warn",
  unavailable: "warn",
  not_comparable: "warn",
  running: "warn",
  queued: "warn",
  cancelled: "neutral",
  pending: "neutral",
  unchanged: "neutral",
  disabled: "neutral",
};

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string | null }) {
  if (status === null) return <Badge>not evaluated</Badge>;
  return <Badge tone={STATUS_TONES[status] ?? "neutral"}>{humanize(status)}</Badge>;
}

export function ErrorState({
  error,
  title = "Could not load data",
}: {
  error: ApiError;
  title?: string;
}) {
  const hint =
    error.kind === "network"
      ? "Is the backend running? Start it with `uv run uvicorn app.main:app` in backend/."
      : error.kind === "not_found"
        ? "It may have been deleted, or the link is wrong."
        : null;
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
    >
      <p className="font-semibold">{title}</p>
      <p className="mt-1">{error.message}</p>
      {hint ? <p className="mt-1">{hint}</p> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-300 p-6 text-sm dark:border-zinc-700">
      <p className="font-medium">{title}</p>
      {children ? <div className="mt-1 text-zinc-600 dark:text-zinc-400">{children}</div> : null}
    </div>
  );
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3">
      <span className="text-sm text-zinc-600 dark:text-zinc-400">{label}</span>
      <div
        aria-hidden="true"
        className="h-4 w-2/3 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800"
      />
      <div
        aria-hidden="true"
        className="h-4 w-1/2 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800"
      />
      <div
        aria-hidden="true"
        className="h-24 w-full animate-pulse rounded bg-zinc-200 dark:bg-zinc-800"
      />
    </div>
  );
}

export function DefinitionList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="font-medium text-zinc-600 dark:text-zinc-400">{term}</dt>
          <dd className="min-w-0 break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  if (value === null || value === undefined) return <Missing reason="none recorded" />;
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className="max-h-80 overflow-auto rounded bg-zinc-100 p-3 font-mono text-xs leading-5 dark:bg-zinc-900"
    >
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="font-medium text-blue-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-blue-400"
    >
      {children}
    </Link>
  );
}

/** A scenario's short name with its case underneath: suites with several cases reuse names. */
export function ScenarioLabel({ scenarioId, caseId }: { scenarioId: string; caseId?: string }) {
  return (
    <>
      <span>{scenarioName(scenarioId, caseId)}</span>
      {caseId ? (
        <>
          {" "}
          <span className="block text-xs text-zinc-500">{caseId}</span>
        </>
      ) : null}
    </>
  );
}

export function Table({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full min-w-max text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export const th =
  "px-3 py-2 text-xs font-semibold tracking-wide text-zinc-600 uppercase dark:text-zinc-400";
export const td = "border-t border-zinc-200 px-3 py-2 align-top dark:border-zinc-800";
