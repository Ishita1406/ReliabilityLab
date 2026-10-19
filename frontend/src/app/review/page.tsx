import type { Metadata } from "next";
import { Suspense } from "react";

import { CalibrationSummary, ReviewQueue } from "@/components/review-queue";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";

export const metadata: Metadata = { title: "Review queue" };

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

async function QueueData({ searchParams }: { searchParams: PageProps<"/review">["searchParams"] }) {
  const params = await searchParams;
  const selected = typeof params.experiment === "string" ? params.experiment : "";
  const experiments = await api.experiments();
  if (!experiments.ok) return <ErrorState error={experiments.error} />;
  const withEvaluator = experiments.data.filter((e) => e.evaluator_version !== null);
  const picker = (
    <form method="get" action="/review" className="mb-6 flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm">
        Experiment
        <select name="experiment" defaultValue={selected} className={field} required>
          <option value="">Choose…</option>
          {withEvaluator.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name} · {e.status}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="rounded bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
      >
        Show queue
      </button>
    </form>
  );
  if (!selected) {
    return (
      <>
        {picker}
        <EmptyState title="Choose an experiment">
          The queue lists security-sensitive, disputed, uncertain, and randomly sampled runs.
        </EmptyState>
      </>
    );
  }
  const [queue, calibration] = await Promise.all([
    api.reviewQueue(selected),
    api.calibration(selected, "sampled"),
  ]);
  return (
    <>
      {picker}
      {queue.ok ? <ReviewQueue items={queue.data} /> : <ErrorState error={queue.error} />}
      {calibration.ok ? (
        <CalibrationSummary report={calibration.data} />
      ) : (
        <ErrorState error={calibration.error} title="Could not load calibration" />
      )}
    </>
  );
}

export default function ReviewQueuePage(props: PageProps<"/review">) {
  return (
    <>
      <PageHeader
        title="Review queue"
        description="Runs that need human judgment, and how the evaluator agrees with the reviews collected so far."
      />
      <Suspense fallback={<LoadingState label="Loading review queue…" />}>
        <QueueData searchParams={props.searchParams} />
      </Suspense>
    </>
  );
}
