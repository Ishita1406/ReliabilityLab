import type { Metadata } from "next";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { NewExperimentForm } from "@/components/new-experiment-form";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Missing,
  PageHeader,
  Section,
  StatusBadge,
  Table,
  TextLink,
  td,
  th,
} from "@/components/ui";
import { api } from "@/lib/api/client";
import { dateTime } from "@/lib/format";

import { createExperimentAction } from "./actions";

export const metadata: Metadata = { title: "Experiments" };

async function NewExperiment() {
  const [suites, versions] = await Promise.all([api.suites(), api.agentVersions()]);
  if (!suites.ok) return <ErrorState error={suites.error} title="Could not load suites" />;
  if (!versions.ok)
    return <ErrorState error={versions.error} title="Could not load agent versions" />;
  if (suites.data.length === 0 || versions.data.length === 0) {
    return (
      <EmptyState title="Nothing to run yet">
        Register a suite and an agent version first (e.g.{" "}
        <code>uv run python -m app.seed_demo</code>).
      </EmptyState>
    );
  }
  return (
    <NewExperimentForm
      suites={suites.data}
      agentVersions={versions.data}
      action={createExperimentAction}
    />
  );
}

async function ExperimentList() {
  const result = await api.experiments();
  if (!result.ok) return <ErrorState error={result.error} />;
  if (result.data.length === 0) return <EmptyState title="No experiments stored yet" />;
  const active = result.data.some((e) => e.status === "queued" || e.status === "running");
  return (
    <>
      <AutoRefresh active={active} label="Queued or running experiments" />
      <Table caption="Experiments">
        <thead>
          <tr>
            <th className={th}>Experiment</th>
            <th className={th}>Status</th>
            <th className={th}>Suite</th>
            <th className={th}>Agent</th>
            <th className={th}>Runs stored / planned</th>
            <th className={th}>Evaluator</th>
            <th className={th}>Created</th>
          </tr>
        </thead>
        <tbody>
          {result.data.map((experiment) => (
            <tr key={experiment.id}>
              <td className={td}>
                <TextLink href={`/experiments/${experiment.id}`}>{experiment.name}</TextLink>
              </td>
              <td className={td}>
                <StatusBadge status={experiment.status} />
              </td>
              <td className={`${td} font-mono text-xs`}>
                {experiment.suite_id}@{experiment.suite_version}
              </td>
              <td className={td}>
                {experiment.agent_version_label}
                <span className="block text-xs text-zinc-500">{experiment.agent_model}</span>
              </td>
              <td className={`${td} tabular-nums`}>
                {experiment.stored_runs} / {experiment.planned_runs}
              </td>
              <td className={`${td} font-mono text-xs`}>
                {experiment.evaluator_version ?? <Missing reason="no snapshot" />}
              </td>
              <td className={td}>{dateTime(experiment.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  );
}

export default function ExperimentsPage() {
  return (
    <>
      <PageHeader title="Experiments" description="Stored experiments, newest first." />
      <Section
        title="New experiment"
        description="Runs in the background on a worker; this page returns immediately and shows progress."
      >
        <Suspense fallback={<LoadingState label="Loading options…" />}>
          <NewExperiment />
        </Suspense>
      </Section>
      <Suspense fallback={<LoadingState label="Loading experiments…" />}>
        <ExperimentList />
      </Suspense>
    </>
  );
}
