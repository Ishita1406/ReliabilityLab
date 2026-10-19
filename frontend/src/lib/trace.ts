/** Pure helpers for the trace explorer (tree structure, filtering, sorting, timeline). */
import type { TraceEvent } from "./api/types";

export type TraceSort = "sequence" | "duration";

export interface TraceFilters {
  kinds: string[];
  status: string;
  query: string;
  sort: TraceSort;
}

export const DEFAULT_TRACE_FILTERS: TraceFilters = {
  kinds: [],
  status: "",
  query: "",
  sort: "sequence",
};

export function durationMs(event: TraceEvent): number {
  return new Date(event.ended_at).getTime() - new Date(event.started_at).getTime();
}

/** Depth of every event in the parent-child tree (root = 0). */
export function depths(events: TraceEvent[]): Map<string, number> {
  const byId = new Map(events.map((e) => [e.id, e]));
  const result = new Map<string, number>();
  const depthOf = (event: TraceEvent): number => {
    const known = result.get(event.id);
    if (known !== undefined) return known;
    const parent = event.parent_id ? byId.get(event.parent_id) : undefined;
    const depth = parent ? depthOf(parent) + 1 : 0;
    result.set(event.id, depth);
    return depth;
  };
  events.forEach(depthOf);
  return result;
}

export function children(events: TraceEvent[], id: string): TraceEvent[] {
  return events.filter((e) => e.parent_id === id);
}

/** Text searched by the query filter: tool name, error type, ids, and JSON payloads. */
function searchable(event: TraceEvent): string {
  return [
    event.kind,
    event.tool_name,
    event.error_type,
    event.fault_id,
    event.id,
    JSON.stringify(event.arguments),
    JSON.stringify(event.payload),
    JSON.stringify(event.error_details),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export function filterEvents(events: TraceEvent[], filters: TraceFilters): TraceEvent[] {
  const query = filters.query.trim().toLowerCase();
  const kept = events.filter(
    (e) =>
      (filters.kinds.length === 0 || filters.kinds.includes(e.kind)) &&
      (filters.status === "" || e.status === filters.status) &&
      (query === "" || searchable(e).includes(query)),
  );
  if (filters.sort === "duration") {
    return [...kept].sort((a, b) => durationMs(b) - durationMs(a) || a.sequence - b.sequence);
  }
  return [...kept].sort((a, b) => a.sequence - b.sequence);
}

/** Offset and width of an event within the run's time span, as fractions of 0..1. */
export function timelineSpan(
  event: TraceEvent,
  root: TraceEvent,
): { offset: number; width: number } {
  const start = new Date(root.started_at).getTime();
  const total = new Date(root.ended_at).getTime() - start;
  if (total <= 0) return { offset: 0, width: 1 };
  const offset = (new Date(event.started_at).getTime() - start) / total;
  const width = durationMs(event) / total;
  return { offset: Math.min(Math.max(offset, 0), 1), width: Math.min(Math.max(width, 0), 1) };
}

export function eventTitle(event: TraceEvent): string {
  const base = event.kind.replaceAll("_", " ");
  if (event.fault_id)
    return `${base}: ${event.fault_id} (${event.fault_type?.replaceAll("_", " ")})`;
  if (event.tool_name) return `${base}: ${event.tool_name}`;
  return base;
}
