"use client";

import type { RunDetail, TraceEvent } from "@/lib/api/types";
import { humanize } from "@/lib/format";

import { EventLinks } from "./evaluation-panel";
import { Badge, EmptyState, Missing, Section } from "./ui";

/**
 * Observed facts and inferred candidate causes are rendered in separate, separately labelled
 * regions so an inference is never presented as an observation.
 */
export function DiagnosisPanel({
  detail,
  events,
  onSelectEvent,
}: {
  detail: RunDetail;
  events: TraceEvent[];
  onSelectEvent: (id: string) => void;
}) {
  const diagnosis = detail.diagnosis;
  return (
    <Section title="Failure diagnosis">
      {diagnosis === null ? (
        <Missing reason={detail.diagnosis_unavailable_reason} />
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <section
            aria-labelledby="observed-heading"
            className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
          >
            <h3 id="observed-heading" className="font-semibold">
              Observed (facts from the trace)
            </h3>
            <ol className="mt-3 flex flex-col gap-3 text-sm">
              {diagnosis.observations.map((observation) => (
                <li key={observation.id} id={`observation-${observation.id}`}>
                  <span className="font-mono text-xs text-zinc-500">{observation.id}</span>{" "}
                  <Badge>{humanize(observation.kind)}</Badge> {observation.statement}
                  {observation.excerpt ? (
                    <q className="mt-1 block rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-900">
                      {observation.excerpt}
                    </q>
                  ) : null}
                  <EventLinks
                    ids={observation.event_ids}
                    events={events}
                    onSelectEvent={onSelectEvent}
                  />
                </li>
              ))}
            </ol>
          </section>
          <section
            aria-labelledby="inferred-heading"
            className="rounded-lg border border-dashed border-amber-400 p-4 dark:border-amber-700"
          >
            <h3 id="inferred-heading" className="font-semibold">
              Inferred candidate root causes (hypotheses, not proven)
            </h3>
            <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{diagnosis.disclaimer}</p>
            {diagnosis.candidates.length === 0 ? (
              <div className="mt-3">
                <EmptyState title="No candidate causes">
                  {diagnosis.verdict === "passed"
                    ? "The run passed; nothing needs explaining."
                    : "No rule produced a hypothesis."}
                </EmptyState>
              </div>
            ) : (
              <ol className="mt-3 flex flex-col gap-3 text-sm">
                {diagnosis.candidates.map((candidate, index) => (
                  <li key={index}>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{humanize(candidate.cause)}</span>
                      <Badge tone={candidate.strength === "direct" ? "neutral" : "warn"}>
                        {candidate.strength} evidence
                      </Badge>
                      <Badge>{candidate.basis === "llm" ? "LLM suggestion" : "rule"}</Badge>
                    </span>
                    <p className="mt-1">{candidate.explanation}</p>
                    <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                      Based on observations{" "}
                      {candidate.observation_ids.map((id) => (
                        <a key={id} href={`#observation-${id}`} className="font-mono underline">
                          {id}{" "}
                        </a>
                      ))}
                      {candidate.criterion_ids.length
                        ? `· may explain ${candidate.criterion_ids.join(", ")}`
                        : ""}
                    </p>
                  </li>
                ))}
              </ol>
            )}
            {diagnosis.unexplained_criterion_ids.length ? (
              <p className="mt-3 text-sm">
                Unexplained failures: {diagnosis.unexplained_criterion_ids.join(", ")}
              </p>
            ) : null}
          </section>
        </div>
      )}
    </Section>
  );
}
