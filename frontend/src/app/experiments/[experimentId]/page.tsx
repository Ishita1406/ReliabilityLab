import { Suspense } from "react";

import { ExperimentView } from "@/components/experiment-view";
import { JobPanel } from "@/components/job-panel";
import { ErrorState, LoadingState, PageHeader, TextLink } from "@/components/ui";
import { api } from "@/lib/api/client";

import { cancelExperimentAction, submitExperimentAction } from "../actions";

async function ExperimentData({
  params,
}: {
  params: PageProps<"/experiments/[experimentId]">["params"];
}) {
  const { experimentId } = await params;
  const [detail, status] = await Promise.all([
    api.experiment(experimentId),
    api.experimentStatus(experimentId),
  ]);
  if (!detail.ok) return <ErrorState error={detail.error} title="Could not load the experiment" />;
  const jobPanel = status.ok ? (
    <JobPanel
      job={status.data}
      submitAction={submitExperimentAction.bind(null, experimentId)}
      cancelAction={cancelExperimentAction.bind(null, experimentId)}
    />
  ) : (
    <ErrorState error={status.error} title="Could not load the job status" />
  );
  return (
    <>
      <h2 className="mb-1 text-xl font-semibold">{detail.data.experiment.name}</h2>
      <p className="mb-6 font-mono text-xs text-zinc-500">{detail.data.experiment.id}</p>
      <ExperimentView data={detail.data} jobPanel={jobPanel} />
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
