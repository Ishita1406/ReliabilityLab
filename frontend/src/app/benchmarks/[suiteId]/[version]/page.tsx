import { Suspense } from "react";

import { CaseExplorer } from "@/components/case-explorer";
import { Badge, ErrorState, LoadingState, PageHeader, TextLink } from "@/components/ui";
import { api } from "@/lib/api/client";

async function SuiteData({
  params,
}: {
  params: PageProps<"/benchmarks/[suiteId]/[version]">["params"];
}) {
  const { suiteId, version } = await params;
  const result = await api.suite(suiteId, version);
  if (!result.ok)
    return <ErrorState error={result.error} title={`Could not load ${suiteId}@${version}`} />;
  const suite = result.data;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <h2 className="text-lg font-semibold">
          {suite.name}{" "}
          <span className="font-mono text-sm text-zinc-500">
            {suite.id}@{suite.version}
          </span>
        </h2>
        {suite.synthetic ? <Badge tone="warn">synthetic</Badge> : null}
        <span className="text-zinc-600 dark:text-zinc-400">{suite.description}</span>
      </div>
      <CaseExplorer suite={suite} />
    </>
  );
}

export default function SuitePage(props: PageProps<"/benchmarks/[suiteId]/[version]">) {
  return (
    <>
      <PageHeader
        title="Benchmark explorer"
        description={
          <>
            Cases, tags, scenarios, and expected outcomes.{" "}
            <TextLink href="/benchmarks">All suites</TextLink>
          </>
        }
      />
      <Suspense fallback={<LoadingState label="Loading suite…" />}>
        <SuiteData params={props.params} />
      </Suspense>
    </>
  );
}
