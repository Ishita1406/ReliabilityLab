"use client";

import { useMemo, useState } from "react";

import type { RunSummary } from "@/lib/api/types";
import { humanize, percent, seconds, shortId } from "@/lib/format";

import { EmptyState, Missing, ScenarioLabel, StatusBadge, Table, TextLink, td, th } from "./ui";

type RunSort = "scenario" | "duration" | "score";

export function sortRuns(runs: RunSummary[], sort: RunSort): RunSummary[] {
  const missingLast = (value: number | null, direction: 1 | -1) =>
    value === null ? Number.POSITIVE_INFINITY : value * direction;
  const sorted = [...runs];
  if (sort === "duration") {
    sorted.sort(
      (a, b) => missingLast(a.duration_seconds, -1) - missingLast(b.duration_seconds, -1),
    );
  } else if (sort === "score") {
    sorted.sort((a, b) => missingLast(a.score, 1) - missingLast(b.score, 1));
  } else {
    sorted.sort(
      (a, b) => a.scenario_id.localeCompare(b.scenario_id) || a.repetition - b.repetition,
    );
  }
  return sorted;
}

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

export function RunsTable({ runs }: { runs: RunSummary[] }) {
  const [verdict, setVerdict] = useState("");
  const [sort, setSort] = useState<RunSort>("scenario");
  const rows = useMemo(() => {
    const filtered = runs.filter((r) =>
      verdict === "" ? true : verdict === "none" ? r.verdict === null : r.verdict === verdict,
    );
    return sortRuns(filtered, sort);
  }, [runs, verdict, sort]);

  if (runs.length === 0) return <EmptyState title="No runs stored for this experiment yet" />;
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Verdict
          <select className={field} value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="">All</option>
            <option value="passed">passed</option>
            <option value="failed">failed</option>
            <option value="inconclusive">inconclusive</option>
            <option value="none">not evaluated</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Sort by
          <select
            className={field}
            value={sort}
            onChange={(e) => setSort(e.target.value as RunSort)}
          >
            <option value="scenario">Scenario</option>
            <option value="duration">Slowest first</option>
            <option value="score">Lowest score first</option>
          </select>
        </label>
      </div>
      <p role="status" className="mb-2 text-sm text-zinc-600 dark:text-zinc-400">
        Showing {rows.length} of {runs.length} runs
      </p>
      <Table caption="Runs">
        <thead>
          <tr>
            <th className={th}>Scenario</th>
            <th className={th}>Rep.</th>
            <th className={th}>Run status</th>
            <th className={th}>Verdict</th>
            <th className={th}>Score</th>
            <th className={th}>Failure labels</th>
            <th className={th}>Duration</th>
            <th className={th}>Trace</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => (
            <tr key={run.run_id}>
              <td className={td}>
                <ScenarioLabel scenarioId={run.scenario_id} caseId={run.case_id} />
              </td>
              <td className={`${td} tabular-nums`}>{run.repetition}</td>
              <td className={td}>
                <StatusBadge status={run.status} />
              </td>
              <td className={td}>
                <StatusBadge status={run.verdict} />
              </td>
              <td className={`${td} tabular-nums`}>
                {run.score === null ? <Missing /> : percent(run.score, 0)}
              </td>
              <td className={td}>
                {run.labels.length ? run.labels.map(humanize).join(", ") : "—"}
              </td>
              <td className={`${td} tabular-nums`}>
                {run.duration_seconds === null ? <Missing /> : seconds(run.duration_seconds)}
              </td>
              <td className={`${td} font-mono text-xs`}>
                <TextLink href={`/runs/${run.run_id}`}>{shortId(run.run_id)}</TextLink>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  );
}
