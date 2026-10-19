import { Suspense } from "react";

import { RunHeader } from "@/components/run-header";
import { TraceExplorer } from "@/components/trace-explorer";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";

async function RunData({ params }: { params: PageProps<"/runs/[runId]">["params"] }) {
  const { runId } = await params;
  const result = await api.run(runId);
  if (!result.ok) return <ErrorState error={result.error} title="Could not load the run" />;
  return (
    <>
      <RunHeader detail={result.data} />
      <TraceExplorer detail={result.data} />
    </>
  );
}

export default function RunPage(props: PageProps<"/runs/[runId]">) {
  return (
    <>
      <PageHeader
        title="Trace explorer"
        description="Recorded events of one run, its evaluation evidence, and rule-based diagnosis."
      />
      <Suspense fallback={<LoadingState label="Loading run…" />}>
        <RunData params={props.params} />
      </Suspense>
    </>
  );
}
