import type { ExperimentMetrics, Overview } from "@/lib/api/types";
import { dateTime, humanize, metricText, percent, scenarioName, shortId } from "@/lib/format";

import { FailureCategories } from "./failure-categories";
import {
  EmptyState,
  MetricTile,
  Missing,
  Section,
  StatusBadge,
  Table,
  TextLink,
  td,
  th,
} from "./ui";

function ProgressText({ stored, planned }: { stored: number; planned: number }) {
  return (
    <span className="tabular-nums">
      {stored} / {planned} runs
    </span>
  );
}

export function HeadlineMetrics({ metrics }: { metrics: ExperimentMetrics }) {
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <MetricTile label="Task success" metric={metrics.task_success_rate} />
      <MetricTile label="Evaluator uncertainty" metric={metrics.evaluator_uncertainty_rate} />
      <MetricTile label="Fault recovery" metric={metrics.fault_recovery_rate} />
      <MetricTile label="Unauthorized actions" metric={metrics.unauthorized_action_rate} />
      <MetricTile label="Latency (mean)" metric={metrics.latency_mean} />
      <MetricTile label="Latency (p95)" metric={metrics.latency_p95} />
      <MetricTile label="Cost per run" metric={metrics.cost_per_run_mean} />
      <MetricTile label="Total cost" metric={metrics.cost_total} />
    </dl>
  );
}

export function OverviewView({ data }: { data: Overview }) {
  if (data.total_experiments === 0) {
    return (
      <EmptyState title="No experiments stored yet">
        Create and run an experiment with the backend (see backend/README.md), or seed clearly
        labelled demo data with <code>uv run python -m app.seed_demo</code>.
      </EmptyState>
    );
  }
  const latest = data.latest_completed;
  return (
    <>
      <Section title="Experiments by status">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
            <dt className="text-xs font-medium tracking-wide text-zinc-500 uppercase">Total</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums">{data.total_experiments}</dd>
          </div>
          {Object.entries(data.experiment_counts).map(([status, count]) => (
            <div
              key={status}
              className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
            >
              <dt className="text-xs font-medium tracking-wide text-zinc-500 uppercase">
                {humanize(status)}
              </dt>
              <dd className="mt-1 text-2xl font-semibold tabular-nums">{count}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section
        title="Latest completed experiment"
        description="Metrics are per experiment; runs from different configurations are never pooled."
      >
        {latest ? (
          <>
            <p className="mb-3 text-sm">
              <TextLink href={`/experiments/${latest.experiment.id}`}>
                {latest.experiment.name}
              </TextLink>{" "}
              · agent {latest.experiment.agent_version_label} · {latest.experiment.suite_id}@
              {latest.experiment.suite_version} · finished{" "}
              {dateTime(latest.experiment.finished_at) ?? <Missing />}
            </p>
            <HeadlineMetrics metrics={latest.metrics} />
            <h3 className="mt-6 mb-2 text-base font-semibold">Failure categories</h3>
            <FailureCategories metrics={latest.metrics} />
          </>
        ) : (
          <EmptyState title="No completed experiment yet">
            Metrics appear once an experiment has finished running.
          </EmptyState>
        )}
      </Section>

      <Section title="Recent experiments">
        <Table caption="Recent experiments">
          <thead>
            <tr>
              <th className={th}>Experiment</th>
              <th className={th}>Status</th>
              <th className={th}>Agent</th>
              <th className={th}>Progress</th>
              <th className={th}>Task success</th>
            </tr>
          </thead>
          <tbody>
            {data.recent_experiments.map(({ experiment, metrics }) => {
              const success = metricText(metrics.task_success_rate);
              return (
                <tr key={experiment.id}>
                  <td className={td}>
                    <TextLink href={`/experiments/${experiment.id}`}>{experiment.name}</TextLink>
                  </td>
                  <td className={td}>
                    <StatusBadge status={experiment.status} />
                  </td>
                  <td className={td}>{experiment.agent_version_label}</td>
                  <td className={td}>
                    <ProgressText
                      stored={experiment.stored_runs}
                      planned={experiment.planned_runs}
                    />
                  </td>
                  <td className={`${td} tabular-nums`}>
                    {success ?? <Missing reason={metrics.task_success_rate.unavailable_reason} />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Section>

      <Section title="Recent runs">
        {data.recent_runs.length === 0 ? (
          <EmptyState title="No runs stored yet" />
        ) : (
          <Table caption="Recent runs">
            <thead>
              <tr>
                <th className={th}>Run</th>
                <th className={th}>Scenario</th>
                <th className={th}>Run status</th>
                <th className={th}>Verdict</th>
                <th className={th}>Score</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_runs.map((run) => (
                <tr key={run.run_id}>
                  <td className={`${td} font-mono text-xs`}>
                    <TextLink href={`/runs/${run.run_id}`}>{shortId(run.run_id)}</TextLink>
                  </td>
                  <td className={td}>{scenarioName(run.scenario_id, run.case_id)}</td>
                  <td className={td}>
                    <StatusBadge status={run.status} />
                  </td>
                  <td className={td}>
                    <StatusBadge status={run.verdict} />
                  </td>
                  <td className={`${td} tabular-nums`}>
                    {run.score === null ? (
                      <Missing reason="not evaluated" />
                    ) : (
                      percent(run.score, 0)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>
    </>
  );
}
