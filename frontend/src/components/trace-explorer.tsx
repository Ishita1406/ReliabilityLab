"use client";

import { useMemo, useState } from "react";

import type { RunDetail, TraceEvent } from "@/lib/api/types";
import { humanize, seconds } from "@/lib/format";
import {
  DEFAULT_TRACE_FILTERS,
  type TraceFilters,
  type TraceSort,
  children,
  depths,
  durationMs,
  eventTitle,
  filterEvents,
  timelineSpan,
} from "@/lib/trace";

import { DiagnosisPanel } from "./diagnosis-panel";
import { EvaluationPanel } from "./evaluation-panel";
import { Badge, DefinitionList, EmptyState, JsonBlock, Missing, Section, StatusBadge } from "./ui";

const KINDS = [
  "run",
  "agent_decision",
  "tool_call",
  "tool_result",
  "fault_injected",
  "final_answer",
  "run_error",
] as const;

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

function EventButton({
  event,
  label,
  onSelect,
}: {
  event: TraceEvent;
  label?: string;
  onSelect: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(event.id)}
      className="text-left font-medium text-blue-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-blue-400"
    >
      {label ?? `#${event.sequence} ${eventTitle(event)}`}
    </button>
  );
}

function EventDetail({
  event,
  events,
  onSelect,
}: {
  event: TraceEvent;
  events: TraceEvent[];
  onSelect: (id: string) => void;
}) {
  const parent = events.find((e) => e.id === event.parent_id);
  const kids = children(events, event.id);
  return (
    <div className="flex flex-col gap-4 text-sm">
      <h3 className="text-base font-semibold">
        #{event.sequence} {eventTitle(event)} <StatusBadge status={event.status} />
      </h3>
      <DefinitionList
        items={[
          [
            "Event ID",
            <code key="id" className="text-xs">
              {event.id}
            </code>,
          ],
          [
            "Parent",
            parent ? <EventButton key="p" event={parent} onSelect={onSelect} /> : "none (run root)",
          ],
          [
            "Children",
            kids.length ? (
              <ul key="c" className="flex flex-col">
                {kids.map((kid) => (
                  <li key={kid.id}>
                    <EventButton event={kid} onSelect={onSelect} />
                  </li>
                ))}
              </ul>
            ) : (
              "none"
            ),
          ],
          ["Started", event.started_at],
          ["Duration", seconds(durationMs(event) / 1000)],
          ...(event.tool_name ? [["Tool", event.tool_name] as [string, string]] : []),
          ...(event.tool_call_id ? [["Tool call", event.tool_call_id] as [string, string]] : []),
          ...(event.fault_id
            ? [
                ["Injected fault", `${event.fault_id} (${humanize(event.fault_type ?? "")})`] as [
                  string,
                  string,
                ],
              ]
            : []),
          ...(event.error_type ? [["Error", event.error_type] as [string, string]] : []),
        ]}
      />
      {event.kind === "tool_call" ? (
        <div>
          <h4 className="mb-1 font-semibold">Tool input (arguments)</h4>
          <JsonBlock value={event.arguments} label="Tool arguments" />
        </div>
      ) : null}
      {event.payload !== null ? (
        <div>
          <h4 className="mb-1 font-semibold">
            {event.kind === "tool_result" ? "Tool output" : "Payload"}
            {event.payload_truncated ? (
              <Badge tone="warn">
                truncated
                {event.payload_original_bytes !== null
                  ? ` from ${event.payload_original_bytes} bytes`
                  : ""}
              </Badge>
            ) : null}
          </h4>
          <JsonBlock value={event.payload} label="Event payload" />
        </div>
      ) : null}
      {event.error_details !== null ? (
        <div>
          <h4 className="mb-1 font-semibold">Error details</h4>
          <JsonBlock value={event.error_details} label="Error details" />
        </div>
      ) : null}
      {Object.keys(event.attributes).length ? (
        <div>
          <h4 className="mb-1 font-semibold">Attributes</h4>
          <JsonBlock value={event.attributes} label="Event attributes" />
        </div>
      ) : null}
    </div>
  );
}

export function TraceExplorer({ detail }: { detail: RunDetail }) {
  const events = useMemo(() => detail.trace?.events ?? [], [detail.trace]);
  const [filters, setFilters] = useState<TraceFilters>(DEFAULT_TRACE_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(events[0]?.id ?? null);
  const depthMap = useMemo(() => depths(events), [events]);
  const visible = useMemo(() => filterEvents(events, filters), [events, filters]);
  const root = events[0];
  const selected = events.find((e) => e.id === selectedId) ?? null;
  const update = (patch: Partial<TraceFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const select = (id: string) => {
    setSelectedId(id);
    document.getElementById("trace-event-detail")?.focus();
  };

  return (
    <>
      <Section
        title="Trace timeline"
        description="Observed events, as recorded by the harness. Indentation shows parent-child structure."
      >
        {detail.trace === null || root === undefined ? (
          <EmptyState title="Trace unavailable">{detail.trace_unavailable_reason}</EmptyState>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div>
              <form
                role="search"
                aria-label="Filter trace events"
                className="mb-3 flex flex-wrap items-end gap-3"
                onSubmit={(e) => e.preventDefault()}
              >
                <fieldset className="flex flex-wrap gap-2 text-sm">
                  <legend className="mb-1">Event kinds</legend>
                  {KINDS.map((kind) => (
                    <label key={kind} className="flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={filters.kinds.includes(kind)}
                        onChange={(e) =>
                          update({
                            kinds: e.target.checked
                              ? [...filters.kinds, kind]
                              : filters.kinds.filter((k) => k !== kind),
                          })
                        }
                      />
                      {humanize(kind)}
                    </label>
                  ))}
                </fieldset>
                <label className="flex flex-col gap-1 text-sm">
                  Status
                  <select
                    className={field}
                    value={filters.status}
                    onChange={(e) => update({ status: e.target.value })}
                  >
                    <option value="">All</option>
                    <option value="ok">ok</option>
                    <option value="error">error</option>
                    <option value="timeout">timeout</option>
                    <option value="rejected">rejected</option>
                  </select>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  Search
                  <input
                    type="search"
                    className={field}
                    value={filters.query}
                    onChange={(e) => update({ query: e.target.value })}
                    placeholder="tool, error, payload…"
                  />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  Order
                  <select
                    className={field}
                    value={filters.sort}
                    onChange={(e) => update({ sort: e.target.value as TraceSort })}
                  >
                    <option value="sequence">Sequence (tree)</option>
                    <option value="duration">Longest first</option>
                  </select>
                </label>
              </form>
              <p role="status" className="mb-2 text-sm text-zinc-600 dark:text-zinc-400">
                Showing {visible.length} of {events.length} events
              </p>
              <ol aria-label="Trace events" className="flex flex-col gap-1">
                {visible.map((event) => {
                  const span = timelineSpan(event, root);
                  const isSelected = event.id === selectedId;
                  const indent = filters.sort === "sequence" ? (depthMap.get(event.id) ?? 0) : 0;
                  return (
                    <li key={event.id}>
                      <button
                        type="button"
                        aria-pressed={isSelected}
                        onClick={() => setSelectedId(event.id)}
                        className={`w-full rounded border px-2 py-1.5 text-left text-sm focus-visible:outline-2 focus-visible:outline-blue-600 ${
                          isSelected
                            ? "border-blue-600 bg-blue-50 dark:border-blue-400 dark:bg-blue-950"
                            : "border-transparent hover:bg-zinc-100 dark:hover:bg-zinc-900"
                        }`}
                        style={{ paddingLeft: `${0.5 + indent * 1.25}rem` }}
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          {/* Explicit spaces keep the accessible name readable ("#2 tool call: … ok 5 ms"). */}
                          <span className="font-mono text-xs text-zinc-500">#{event.sequence}</span>{" "}
                          <span>{eventTitle(event)}</span> <StatusBadge status={event.status} />{" "}
                          <span className="ml-auto text-xs tabular-nums text-zinc-500">
                            {seconds(durationMs(event) / 1000)}
                          </span>
                        </span>
                        <span
                          aria-hidden="true"
                          className="mt-1 block h-1 rounded bg-zinc-200 dark:bg-zinc-800"
                        >
                          <span
                            className="block h-1 rounded bg-zinc-500"
                            style={{
                              marginLeft: `${span.offset * 100}%`,
                              width: `${Math.max(span.width * 100, 0.5)}%`,
                            }}
                          />
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </div>
            <div
              id="trace-event-detail"
              tabIndex={-1}
              aria-live="polite"
              aria-label="Selected event"
              className="rounded-lg border border-zinc-200 p-4 focus:outline-none dark:border-zinc-800"
            >
              {selected ? (
                <EventDetail event={selected} events={events} onSelect={select} />
              ) : (
                <Missing reason="no event selected" />
              )}
            </div>
          </div>
        )}
      </Section>
      <EvaluationPanel detail={detail} events={events} onSelectEvent={select} />
      <DiagnosisPanel detail={detail} events={events} onSelectEvent={select} />
    </>
  );
}
