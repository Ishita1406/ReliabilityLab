import type { Metadata } from "next";
import { Suspense } from "react";

import { ComparisonView } from "@/components/comparison-view";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { ExperimentSummary } from "@/lib/api/types";

export const metadata: Metadata = { title: "Compare experiments" };

type SearchParams = PageProps<"/compare">["searchParams"];

function single(value: string | string[] | undefined): string {
  return Array.isArray(value) ? (value[0] ?? "") : (value ?? "");
}

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

function Picker({
  experiments,
  baseline,
  candidate,
}: {
  experiments: ExperimentSummary[];
  baseline: string;
  candidate: string;
}) {
  const options = experiments.map((e) => (
    <option key={e.id} value={e.id}>
      {e.name} · {e.agent_version_label} · {e.status}
    </option>
  ));
  return (
    <form method="get" action="/compare" className="mb-6 flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm">
        Baseline
        <select name="baseline" defaultValue={baseline} className={field} required>
          <option value="">Choose…</option>
          {options}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Candidate
        <select name="candidate" defaultValue={candidate} className={field} required>
          <option value="">Choose…</option>
          {options}
        </select>
      </label>
      <button
        type="submit"
        className="rounded bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-blue-600 dark:bg-zinc-100 dark:text-zinc-900"
      >
        Compare
      </button>
    </form>
  );
}

async function CompareData({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const baseline = single(params.baseline);
  const candidate = single(params.candidate);
  const experiments = await api.experiments();
  if (!experiments.ok) return <ErrorState error={experiments.error} />;
  const picker = (
    <Picker experiments={experiments.data} baseline={baseline} candidate={candidate} />
  );
  if (!baseline || !candidate) {
    return (
      <>
        {picker}
        <EmptyState title="Choose two experiments to compare">
          Pick a baseline and a candidate. Comparisons need experiments with configuration
          snapshots; cases whose definitions changed are reported as not comparable.
        </EmptyState>
      </>
    );
  }
  const result = await api.comparison(baseline, candidate);
  return (
    <>
      {picker}
      {result.ok ? (
        <ComparisonView data={result.data} />
      ) : (
        <ErrorState error={result.error} title="These experiments cannot be compared" />
      )}
    </>
  );
}

export default function ComparePage(props: PageProps<"/compare">) {
  return (
    <>
      <PageHeader
        title="Compare experiments"
        description="Side-by-side metrics and case-level regressions between a baseline and a candidate."
      />
      <Suspense fallback={<LoadingState label="Loading comparison…" />}>
        <CompareData searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}
