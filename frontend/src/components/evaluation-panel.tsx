"use client";

import type { Evidence, RunDetail, TraceEvent } from "@/lib/api/types";
import { humanize, percent } from "@/lib/format";

import { Badge, EmptyState, Section, StatusBadge } from "./ui";

export function EventLinks({
  ids,
  events,
  onSelectEvent,
}: {
  ids: string[];
  events: TraceEvent[];
  onSelectEvent: (id: string) => void;
}) {
  if (ids.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-2">
      {ids.map((id) => {
        const event = events.find((e) => e.id === id);
        return event ? (
          <button
            key={id}
            type="button"
            onClick={() => onSelectEvent(id)}
            className="font-mono text-xs text-blue-700 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-blue-400"
          >
            event #{event.sequence}
          </button>
        ) : (
          <span
            key={id}
            className="font-mono text-xs text-zinc-500"
            title="not in the stored trace"
          >
            {id.slice(0, 12)}… (not in trace)
          </span>
        );
      })}
    </span>
  );
}

function EvidenceItem({
  item,
  events,
  onSelectEvent,
}: {
  item: Evidence;
  events: TraceEvent[];
  onSelectEvent: (id: string) => void;
}) {
  return (
    <li className="flex flex-col gap-1">
      <span>
        <Badge>{humanize(item.kind)}</Badge> {item.statement}
      </span>
      {item.excerpt ? (
        <q className="block rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-900">
          {item.excerpt}
        </q>
      ) : null}
      <EventLinks ids={item.event_ids} events={events} onSelectEvent={onSelectEvent} />
    </li>
  );
}

export function EvaluationPanel({
  detail,
  events,
  onSelectEvent,
}: {
  detail: RunDetail;
  events: TraceEvent[];
  onSelectEvent: (id: string) => void;
}) {
  const evaluation = detail.evaluation;
  return (
    <Section
      title="Evaluation evidence"
      description={
        detail.evaluator_version
          ? `Scored by ${detail.evaluator_version}. Evidence links point to the trace events above.`
          : undefined
      }
    >
      {evaluation === null ? (
        <EmptyState title="No stored evaluation for this run" />
      ) : (
        <>
          <p className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            Verdict <StatusBadge status={evaluation.verdict} /> score {percent(evaluation.score)}
            {evaluation.score_upper_bound !== evaluation.score
              ? ` (up to ${percent(evaluation.score_upper_bound)} if inconclusive criteria passed)`
              : ""}{" "}
            · threshold {percent(evaluation.pass_threshold)}
          </p>
          <p className="mb-3 text-sm">{evaluation.explanation}</p>
          <ul className="mb-4 flex flex-wrap gap-2 text-sm" aria-label="Layer verdicts">
            {evaluation.layers.map((layer) => (
              <li key={layer.layer}>
                {humanize(layer.layer)}: <StatusBadge status={layer.verdict} />
              </li>
            ))}
          </ul>
          <ul className="flex flex-col gap-3">
            {evaluation.criteria.map((criterion) => (
              <li
                key={criterion.criterion_id}
                className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800"
              >
                <details open={criterion.status !== "passed"}>
                  <summary className="cursor-pointer">
                    <span className="inline-flex flex-wrap items-center gap-2">
                      <code className="text-xs">{criterion.criterion_id}</code>
                      <StatusBadge status={criterion.status} />
                      <Badge>{humanize(criterion.criterion_type)}</Badge>
                      {criterion.required ? <Badge tone="warn">required</Badge> : null}
                      {criterion.implicit ? <Badge>implicit</Badge> : null}
                      {criterion.evaluator === "llm_judge" ? (
                        <Badge tone="warn">LLM judge</Badge>
                      ) : null}
                    </span>
                  </summary>
                  <p className="mt-2">{criterion.explanation}</p>
                  {criterion.confidence !== null ? (
                    <p className="mt-1 text-zinc-600">
                      Judge confidence {percent(criterion.confidence)}
                    </p>
                  ) : null}
                  {criterion.evidence.length ? (
                    <ul className="mt-2 flex flex-col gap-2">
                      {criterion.evidence.map((item, index) => (
                        <EvidenceItem
                          key={index}
                          item={item}
                          events={events}
                          onSelectEvent={onSelectEvent}
                        />
                      ))}
                    </ul>
                  ) : null}
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  );
}
