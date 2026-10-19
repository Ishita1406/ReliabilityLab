import { describe, expect, it } from "vitest";

import { fixtures } from "@/test/fixtures";

import type { MetricValue } from "./api/types";
import { metricDetail, metricText, percent, scenarioName, seconds } from "./format";
import { parseCorrection, parseSubmission } from "./review-form";
import { DEFAULT_TRACE_FILTERS, depths, filterEvents, timelineSpan } from "./trace";

const unavailable: MetricValue = {
  value: null,
  unit: "ratio",
  sample_unit: "runs",
  numerator: null,
  denominator: 0,
  ci_low: null,
  ci_high: null,
  unavailable_reason: "no evaluated runs",
};

describe("format", () => {
  it("never formats a missing metric as zero", () => {
    expect(metricText(unavailable)).toBeNull();
    expect(metricDetail(unavailable)).toBeNull();
  });

  it("formats proportions with sample size and interval", () => {
    const metric = fixtures.experiment.metrics.task_success_rate;
    expect(metricText(metric)).toBe("91.7%");
    expect(metricDetail(metric)).toBe("11 / 12 runs, 95% CI 64.6%–98.5%");
  });

  it("formats durations and scenario names", () => {
    expect(seconds(0.12)).toBe("120 ms");
    expect(seconds(2.5)).toBe("2.50 s");
    expect(seconds(75)).toBe("1 min 15 s");
    expect(percent(0.5)).toBe("50.0%");
    expect(scenarioName("runner.lighthouse-year.baseline", "runner.lighthouse-year")).toBe(
      "baseline",
    );
  });
});

describe("trace helpers", () => {
  const events = fixtures.runFailed.trace!.events;

  it("derives tree depth from parent links", () => {
    const depthMap = depths(events);
    expect(depthMap.get(events[0]!.id)).toBe(0);
    const result = events.find((e) => e.kind === "tool_result")!;
    expect(depthMap.get(result.id)).toBe(2); // run -> tool_call -> tool_result
  });

  it("filters by kind, status, and text, and sorts by duration", () => {
    const toolCalls = filterEvents(events, { ...DEFAULT_TRACE_FILTERS, kinds: ["tool_call"] });
    expect(toolCalls.length).toBeGreaterThan(0);
    expect(toolCalls.every((e) => e.kind === "tool_call")).toBe(true);

    const faults = filterEvents(events, { ...DEFAULT_TRACE_FILTERS, query: "mislead" });
    expect(faults.some((e) => e.kind === "fault_injected")).toBe(true);

    const byDuration = filterEvents(events, { ...DEFAULT_TRACE_FILTERS, sort: "duration" });
    expect(byDuration[0]!.kind).toBe("run"); // the root spans the whole run
    expect(filterEvents(events, { ...DEFAULT_TRACE_FILTERS, status: "timeout" })).toEqual([]);
  });

  it("places events on the run's timeline", () => {
    const root = events[0]!;
    expect(timelineSpan(root, root)).toEqual({ offset: 0, width: 1 });
    const last = events[events.length - 1]!;
    const span = timelineSpan(last, root);
    expect(span.offset).toBeGreaterThanOrEqual(0);
    expect(span.offset).toBeLessThanOrEqual(1);
  });
});

function form(entries: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(entries)) data.set(key, value);
  return data;
}

describe("review form parsing", () => {
  it("parses a complete submission", () => {
    const parsed = parseSubmission(
      form({
        reviewer: " alice ",
        label: "fail",
        blind: "on",
        confidence: "80",
        "category.task_correctness": "present",
        "category.tool_failure": "",
        evidence_note: "answer contradicts the almanac",
        evidence_event_id: `evt_${"a".repeat(32)}`,
      }),
    );
    expect(parsed).toEqual({
      ok: true,
      value: {
        reviewer: "alice",
        label: "fail",
        blind: true,
        confidence: 0.8,
        rationale: null,
        categories: { task_correctness: "present" },
        evidence: [
          {
            note: "answer contradicts the almanac",
            event_id: `evt_${"a".repeat(32)}`,
            excerpt: null,
          },
        ],
      },
    });
  });

  it("requires a rationale for ambiguous labels and validates fields", () => {
    const parsed = parseSubmission(
      form({ label: "ambiguous", confidence: "150", evidence_event_id: "nope" }),
    );
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) {
      expect(Object.keys(parsed.errors).sort()).toEqual([
        "confidence",
        "evidence_event_id",
        "evidence_note",
        "rationale",
        "reviewer",
      ]);
    }
  });

  it("requires a reason for corrections", () => {
    const parsed = parseCorrection(form({ label: "pass" }));
    expect(parsed).toEqual({
      ok: false,
      errors: { reason: "Say why you are correcting the review." },
    });
  });
});
