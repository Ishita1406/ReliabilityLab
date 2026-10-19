import type { RunDetail } from "@/lib/api/types";
import { humanize, scenarioName, seconds } from "@/lib/format";

import { DefinitionList, Missing, StatusBadge, TextLink } from "./ui";

export function RunHeader({ detail }: { detail: RunDetail }) {
  const run = detail.run;
  return (
    <div className="mb-8 flex flex-col gap-4">
      <p className="flex flex-wrap items-center gap-3 text-sm">
        <span className="font-mono text-xs">{run.run_id}</span>
        <TextLink href={`/experiments/${run.experiment_id}`}>Experiment</TextLink>
        <TextLink href={`/runs/${run.run_id}/review`}>Review this run</TextLink>
      </p>
      <DefinitionList
        items={[
          ["Case / scenario", `${run.case_id} / ${scenarioName(run.scenario_id, run.case_id)}`],
          ["Run status", <StatusBadge key="s" status={run.status} />],
          ["Termination", humanize(detail.termination_reason)],
          ["Verdict", <StatusBadge key="v" status={run.verdict} />],
          [
            "Duration",
            run.duration_seconds === null ? <Missing key="d" /> : seconds(run.duration_seconds),
          ],
          [
            "Declared outcome",
            detail.declared_outcome ? (
              humanize(detail.declared_outcome)
            ) : (
              <Missing key="o" reason="no final answer" />
            ),
          ],
          [
            "Final answer",
            detail.final_answer ?? (
              <Missing key="a" reason={detail.error_message ?? "no final answer"} />
            ),
          ],
        ]}
      />
    </div>
  );
}
