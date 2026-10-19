import { Suspense } from "react";

import { OverviewView } from "@/components/overview-view";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";

async function OverviewData() {
  const result = await api.overview();
  if (!result.ok) return <ErrorState error={result.error} />;
  return <OverviewView data={result.data} />;
}

export default function HomePage() {
  return (
    <>
      <PageHeader
        title="AI Reliability Lab"
        description="How reliably agents complete benchmark tasks under injected faults, from stored experiments."
      />
      <Suspense fallback={<LoadingState label="Loading overview…" />}>
        <OverviewData />
      </Suspense>
    </>
  );
}
