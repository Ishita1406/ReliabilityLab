import { Suspense } from "react";

import { ExperimentView } from "@/components/experiment-view";
import { ErrorState, LoadingState, PageHeader, TextLink } from "@/components/ui";
import { api } from "@/lib/api/client";

async function ExperimentData({
  params,
}: {
  params: PageProps<"/experiments/[experimentId]">["params"];
}) {
  const { experimentId } = await params;
  const result = await api.experiment(experimentId);
  if (!result.ok) return <ErrorState error={result.error} title="Could not load the experiment" />;
  return (
    <>
      <h2 className="mb-1 text-xl font-semibold">{result.data.experiment.name}</h2>
      <p className="mb-6 font-mono text-xs text-zinc-500">{result.data.experiment.id}</p>
      <ExperimentView data={result.data} />
    </>
  );
}

export default function ExperimentPage(props: PageProps<"/experiments/[experimentId]">) {
  return (
    <>
      <PageHeader
        title="Experiment detail"
        description={<TextLink href="/experiments">All experiments</TextLink>}
      />
      <Suspense fallback={<LoadingState label="Loading experiment…" />}>
        <ExperimentData params={props.params} />
      </Suspense>
    </>
  );
}
