import type { Metadata } from "next";
import { Suspense } from "react";

import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Table,
  TextLink,
  td,
  th,
} from "@/components/ui";
import { api } from "@/lib/api/client";
import { dateTime } from "@/lib/format";

export const metadata: Metadata = { title: "Benchmarks" };

async function SuiteList() {
  const result = await api.suites();
  if (!result.ok) return <ErrorState error={result.error} />;
  if (result.data.length === 0) {
    return (
      <EmptyState title="No benchmark suites registered">
        Suites are registered when an experiment service stores them (or by the demo seed).
      </EmptyState>
    );
  }
  return (
    <Table caption="Benchmark suites">
      <thead>
        <tr>
          <th className={th}>Suite</th>
          <th className={th}>Version</th>
          <th className={th}>Cases</th>
          <th className={th}>Scenarios</th>
          <th className={th}>Registered</th>
        </tr>
      </thead>
      <tbody>
        {result.data.map((suite) => (
          <tr key={`${suite.id}@${suite.version}`}>
            <td className={td}>
              <TextLink href={`/benchmarks/${suite.id}/${suite.version}`}>{suite.name}</TextLink>{" "}
              {suite.synthetic ? <Badge tone="warn">synthetic</Badge> : null}
              <span className="block text-xs text-zinc-500">{suite.description}</span>
            </td>
            <td className={`${td} font-mono text-xs`}>{suite.version}</td>
            <td className={`${td} tabular-nums`}>{suite.case_count}</td>
            <td className={`${td} tabular-nums`}>{suite.scenario_count}</td>
            <td className={td}>{dateTime(suite.registered_at)}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

export default function BenchmarksPage() {
  return (
    <>
      <PageHeader
        title="Benchmarks"
        description="Registered benchmark suites. Suite versions are immutable once stored."
      />
      <Suspense fallback={<LoadingState label="Loading suites…" />}>
        <SuiteList />
      </Suspense>
    </>
  );
}
