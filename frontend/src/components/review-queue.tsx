import type { CalibrationReport, QueueItem } from "@/lib/api/types";
import { humanize, percent, scenarioName } from "@/lib/format";

import { Badge, EmptyState, Missing, Section, StatusBadge, Table, TextLink, td, th } from "./ui";

export function ReviewQueue({ items }: { items: QueueItem[] }) {
  return (
    <Section
      title="Queue"
      description="Open items first, then by priority (security, disputed, uncertain, sampled)."
    >
      {items.length === 0 ? (
        <EmptyState title="Nothing needs review" />
      ) : (
        <Table caption="Review queue">
          <thead>
            <tr>
              <th className={th}>Scenario</th>
              <th className={th}>Reasons</th>
              <th className={th}>Evaluator</th>
              <th className={th}>Reviews</th>
              <th className={th}>Action</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const open = item.current_reviewers.length < item.required_reviews;
              return (
                <tr key={item.run_id}>
                  <td className={td}>{scenarioName(item.scenario_id, item.case_id)}</td>
                  <td className={td}>
                    <span className="flex flex-wrap gap-1">
                      {item.reasons.map((reason) => (
                        <Badge
                          key={reason}
                          tone={reason === "security_sensitive" ? "bad" : "neutral"}
                        >
                          {humanize(reason)}
                        </Badge>
                      ))}
                      {item.blind ? <Badge tone="warn">review blind</Badge> : null}
                    </span>
                  </td>
                  <td className={td}>
                    {item.blind ? (
                      <span className="text-zinc-500">hidden for blind review</span>
                    ) : (
                      <StatusBadge status={item.automated_verdict} />
                    )}
                  </td>
                  <td className={`${td} tabular-nums`}>
                    {item.current_reviewers.length} / {item.required_reviews}{" "}
                    {open ? "(open)" : "(done)"}
                  </td>
                  <td className={td}>
                    <TextLink href={`/runs/${item.run_id}/review`}>Review</TextLink>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Section>
  );
}

export function CalibrationSummary({ report }: { report: CalibrationReport }) {
  const binary = report.automated_vs_human;
  const value = (v: number | null, reason: string | null, asPercent = true) =>
    v === null ? <Missing reason={reason} /> : asPercent ? percent(v) : v.toFixed(3);
  return (
    <Section
      title="Evaluator agreement with human reviews"
      description="Random calibration sample only. Human labels are the reference; the evaluator never is."
    >
      <p className="mb-3">
        <Badge tone={report.empirical ? "good" : "warn"}>
          {report.empirical ? "real human labels" : "not an empirical calibration"}
        </Badge>
      </p>
      <p className="mb-3 text-sm tabular-nums">
        {report.summary.reviewed_runs} reviewed runs · {report.summary.compared} compared ·
        confusion (positive = fail): TP {binary.matrix.tp}, FP {binary.matrix.fp}, FN{" "}
        {binary.matrix.fn}, TN {binary.matrix.tn}
      </p>
      <ul className="mb-3 text-sm">
        <li>Agreement: {value(binary.agreement.value, binary.agreement.unavailable_reason)}</li>
        <li>Precision: {value(binary.precision.value, binary.precision.unavailable_reason)}</li>
        <li>Recall: {value(binary.recall.value, binary.recall.unavailable_reason)}</li>
        <li>
          Cohen&apos;s kappa: {value(binary.kappa.value, binary.kappa.unavailable_reason, false)}
        </li>
      </ul>
      {report.caveats.length ? (
        <ul className="list-disc pl-5 text-sm text-zinc-700 dark:text-zinc-300">
          {report.caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}
