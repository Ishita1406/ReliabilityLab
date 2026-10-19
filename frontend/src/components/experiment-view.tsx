import type { ExperimentConfiguration, ExperimentDetail } from "@/lib/api/types";
import { dateTime, humanize, scenarioName } from "@/lib/format";

import { FailureCategories } from "./failure-categories";
import { HeadlineMetrics } from "./overview-view";
import { RunsTable } from "./runs-table";
import {
  DefinitionList,
  EmptyState,
  Missing,
  Section,
  StatusBadge,
  Table,
  TextLink,
  td,
  th,
} from "./ui";

export function Progress({
  stored,
  evaluated,
  planned,
}: {
  stored: number;
  evaluated: number;
  planned: number;
}) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <progress
        className="h-2 w-64 max-w-full accent-zinc-800 dark:accent-zinc-200"
        value={stored}
        max={Math.max(planned, 1)}
        aria-label="Stored runs"
      />
      <span className="tabular-nums">
        {stored} of {planned} planned runs stored · {evaluated} evaluated
      </span>
    </div>
  );
}

export function ConfigurationSnapshot({
  configuration,
}: {
  configuration: ExperimentConfiguration | null;
}) {
  if (configuration === null) {
    return (
      <EmptyState title="No configuration snapshot">
        This experiment was created before versioned snapshots existed (Milestone 7); it cannot be
        compared.
      </EmptyState>
    );
  }
  const agent = configuration.agent_version;
  const limits = configuration.limits;
  return (
    <>
      <DefinitionList
        items={[
          ["Suite", `${configuration.suite_id}@${configuration.suite_version}`],
          ["Scenarios", `${configuration.scenario_ids.length}`],
          ["Agent version", `${agent.version_label} (${agent.model.provider}/${agent.model.name})`],
          [
            "Implementation",
            `${configuration.agent_implementation.name}@${configuration.agent_implementation.version}`,
          ],
          ["Tools", agent.tools.join(", ") || "none"],
          [
            "Limits",
            `${limits.max_steps} steps, ${limits.max_tool_calls} tool calls, ${limits.deadline_seconds} s deadline`,
          ],
          ["Repetitions / seed", `${configuration.repetitions} / ${configuration.seed}`],
          ["Evaluator", configuration.evaluator_version],
          ["Runner", configuration.runner_version],
        ]}
      />
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer font-medium">System prompt and content hashes</summary>
        <pre className="mt-2 rounded bg-zinc-100 p-3 text-xs whitespace-pre-wrap dark:bg-zinc-900">
          {agent.system_prompt}
        </pre>
        <p className="mt-2 font-mono text-xs break-all">
          suite sha256 {configuration.suite_sha256}
        </p>
        <p className="font-mono text-xs break-all">
          agent sha256 {configuration.agent_version_sha256}
        </p>
      </details>
    </>
  );
}

export function ExperimentView({ data }: { data: ExperimentDetail }) {
  const { experiment, configuration, metrics, runs } = data;
  const problems = runs.filter((r) => r.verdict !== "passed");
  return (
    <>
      <Section title="Status">
        <div className="flex flex-wrap items-start gap-6">
          <div className="flex items-center gap-2">
            <StatusBadge status={experiment.status} />
            <span className="text-sm text-zinc-600 dark:text-zinc-400">
              created {dateTime(experiment.created_at)} · finished{" "}
              {dateTime(experiment.finished_at) ?? <Missing reason="not finished" />}
            </span>
          </div>
          <Progress
            stored={experiment.stored_runs}
            evaluated={experiment.evaluated_runs}
            planned={experiment.planned_runs}
          />
        </div>
        {experiment.description ? <p className="mt-3 text-sm">{experiment.description}</p> : null}
      </Section>

      <Section title="Configuration snapshot">
        <ConfigurationSnapshot configuration={configuration} />
      </Section>

      <Section
        title="Aggregate metrics"
        description="From stored evaluations of this experiment only."
      >
        <HeadlineMetrics metrics={metrics} />
        <h3 className="mt-6 mb-2 text-base font-semibold">Failure categories</h3>
        <FailureCategories metrics={metrics} />
      </Section>

      <Section
        title="Failed and unresolved cases"
        description="Runs whose verdict is failed, inconclusive, or missing."
      >
        {runs.length === 0 ? (
          <EmptyState title="No runs stored yet" />
        ) : problems.length === 0 ? (
          <EmptyState title="Every stored run passed" />
        ) : (
          <Table caption="Failed and unresolved cases">
            <thead>
              <tr>
                <th className={th}>Scenario</th>
                <th className={th}>Verdict</th>
                <th className={th}>Labels</th>
                <th className={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {problems.map((run) => (
                <tr key={run.run_id}>
                  <td className={td}>{scenarioName(run.scenario_id, run.case_id)}</td>
                  <td className={td}>
                    <StatusBadge status={run.verdict} />
                  </td>
                  <td className={td}>{run.labels.map(humanize).join(", ") || "—"}</td>
                  <td className={`${td} flex gap-3`}>
                    <TextLink href={`/runs/${run.run_id}`}>Trace</TextLink>
                    <TextLink href={`/runs/${run.run_id}/review`}>Review</TextLink>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Section>

      <Section title="All runs">
        <RunsTable runs={runs} />
      </Section>
    </>
  );
}
