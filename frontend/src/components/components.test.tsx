import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MetricValue, Overview } from "@/lib/api/types";
import { fixtures } from "@/test/fixtures";

import { CaseExplorer } from "./case-explorer";
import { ComparisonView } from "./comparison-view";
import { ExperimentView } from "./experiment-view";
import { NavLinks, isActive } from "./nav-links";
import { OverviewView } from "./overview-view";
import { CorrectReviewForm, SubmitReviewForm } from "./review-forms";
import { ReviewView, chainsByReviewer } from "./review-view";
import { TraceExplorer } from "./trace-explorer";
import { ErrorState, LoadingState, MetricTile } from "./ui";

describe("shared states", () => {
  it("renders an unavailable metric with its reason, not zero", () => {
    const metric: MetricValue = {
      value: null,
      unit: "seconds",
      sample_unit: "runs",
      numerator: null,
      denominator: 12,
      ci_low: null,
      ci_high: null,
      unavailable_reason: "p95 needs at least 20 runs, have 12",
    };
    render(
      <dl>
        <MetricTile label="Latency (p95)" metric={metric} />
      </dl>,
    );
    expect(screen.getByText(/Not available/)).toHaveTextContent(
      "Not available: p95 needs at least 20 runs, have 12",
    );
    expect(screen.queryByText(/^0/)).not.toBeInTheDocument();
  });

  it("renders loading and error states accessibly", () => {
    render(<LoadingState label="Loading runs…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading runs…");
    render(
      <ErrorState error={{ kind: "network", status: null, message: "Could not reach the API" }} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not reach the API");
    expect(alert).toHaveTextContent("Is the backend running?");
  });
});

describe("navigation", () => {
  it("marks the current section and nothing when the path is unknown", () => {
    const { rerender } = render(<NavLinks pathname="/experiments/exp_1" />);
    expect(screen.getByRole("link", { name: "Experiments" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    rerender(<NavLinks pathname={null} />);
    expect(screen.queryByRole("link", { current: "page" })).not.toBeInTheDocument();
    expect(isActive("/runs/run_1", "/experiments")).toBe(true);
  });
});

describe("OverviewView", () => {
  it("shows counts, the latest completed experiment, and explicit unavailable metrics", () => {
    render(<OverviewView data={fixtures.overview} />);
    expect(
      screen.getByRole("heading", { name: "Latest completed experiment" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Latency (p95)").closest("div")).toHaveTextContent(/Not available/);
    const recent = screen.getByRole("table", { name: "Recent experiments" });
    expect(within(recent).getByText("[demo] pending (0.1.0)")).toBeInTheDocument();
    const pendingRow = within(recent).getByText("[demo] pending (0.1.0)").closest("tr")!;
    expect(pendingRow).toHaveTextContent("Not available: no evaluated runs");
  });

  it("explains how to get data when nothing is stored", () => {
    const empty: Overview = {
      ...fixtures.overview,
      total_experiments: 0,
      latest_completed: null,
      recent_experiments: [],
      recent_runs: [],
    };
    render(<OverviewView data={empty} />);
    expect(screen.getByText("No experiments stored yet")).toBeInTheDocument();
  });
});

describe("CaseExplorer", () => {
  it("filters by text, tag, and outcome and sorts", async () => {
    const user = userEvent.setup();
    render(<CaseExplorer suite={fixtures.suite} />);
    const total = fixtures.suite.cases.length;
    expect(screen.getByRole("status")).toHaveTextContent(`Showing ${total} of ${total} cases`);

    await user.selectOptions(screen.getByLabelText("Expected outcome"), "insufficient_information");
    expect(screen.getByRole("status")).toHaveTextContent(`Showing 1 of ${total} cases`);
    expect(screen.getByText("research.insufficient-evidence")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset" }));
    await user.type(screen.getByLabelText("Search"), "no such case");
    expect(screen.getByText("No cases match these filters")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset" }));
    await user.selectOptions(screen.getByLabelText("Sort by"), "title");
    const caseList = screen.getByRole("list", { name: "Benchmark cases" });
    const titles = [...caseList.querySelectorAll(":scope > li")].map(
      (item) => item.querySelector("summary span span")?.textContent,
    );
    expect(titles).toHaveLength(total);
    expect(titles).toEqual([...titles].sort((a, b) => (a ?? "").localeCompare(b ?? "")));
  });
});

describe("ExperimentView", () => {
  it("lists failed cases with links to the trace and the review", () => {
    render(<ExperimentView data={fixtures.experiment} />);
    const failed = screen.getByRole("table", { name: "Failed and unresolved cases" });
    expect(within(failed).getByText("fetch-misleading")).toBeInTheDocument();
    expect(within(failed).getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/runs\/run_[0-9a-f]+\/review$/),
    );
    expect(screen.getByText("evaluator-0.1.0")).toBeInTheDocument();
  });

  it("shows explicit empty states for a pending experiment", () => {
    render(<ExperimentView data={fixtures.experimentPending} />);
    expect(screen.getAllByText("No runs stored yet").length).toBeGreaterThan(0);
    expect(screen.getByText("0 of 12 planned runs stored · 0 evaluated")).toBeInTheDocument();
  });
});

describe("TraceExplorer", () => {
  const detail = fixtures.runFailed;
  const events = detail.trace!.events;

  it("selects events and shows tool input, output, and parent-child links", async () => {
    const user = userEvent.setup();
    render(<TraceExplorer detail={detail} />);
    const list = screen.getByRole("list", { name: "Trace events" });
    const call = events.find((e) => e.kind === "tool_call")!;
    const button = within(list).getByRole("button", { name: new RegExp(`#${call.sequence} `) });
    await user.click(button);

    expect(button).toHaveAttribute("aria-pressed", "true");
    const panel = screen.getByLabelText("Selected event");
    expect(within(panel).getByRole("heading", { level: 3 })).toHaveTextContent(`#${call.sequence}`);
    expect(within(panel).getByLabelText("Tool arguments")).toHaveTextContent(
      Object.keys(call.arguments as object)[0]!,
    );
    // Navigate to a child (the tool result) from the detail panel.
    const result = events.find((e) => e.parent_id === call.id && e.kind === "tool_result")!;
    await user.click(
      within(panel).getByRole("button", { name: new RegExp(`#${result.sequence} `) }),
    );
    expect(within(panel).getByRole("heading", { level: 3 })).toHaveTextContent(
      `#${result.sequence}`,
    );
    expect(within(panel).getByText("Tool output")).toBeInTheDocument();
  });

  it("filters the timeline and reports how many events are shown", async () => {
    const user = userEvent.setup();
    render(<TraceExplorer detail={detail} />);
    await user.click(screen.getByRole("checkbox", { name: "fault injected" }));
    const faults = events.filter((e) => e.kind === "fault_injected").length;
    expect(screen.getByText(`Showing ${faults} of ${events.length} events`)).toBeInTheDocument();
  });

  it("jumps from evaluation evidence to the referenced event", async () => {
    const user = userEvent.setup();
    render(<TraceExplorer detail={detail} />);
    const evidenceLinks = screen.getAllByRole("button", { name: /^event #\d+$/ });
    const target = evidenceLinks[0]!;
    const sequence = target.textContent!.replace("event #", "");
    await user.click(target);
    expect(
      within(screen.getByLabelText("Selected event")).getByRole("heading", { level: 3 }),
    ).toHaveTextContent(`#${sequence}`);
  });

  it("separates observed facts from inferred hypotheses", () => {
    render(<TraceExplorer detail={detail} />);
    const observed = screen.getByRole("region", { name: "Observed (facts from the trace)" });
    const inferred = screen.getByRole("region", {
      name: "Inferred candidate root causes (hypotheses, not proven)",
    });
    expect(within(observed).getAllByRole("listitem")).toHaveLength(
      detail.diagnosis!.observations.length,
    );
    expect(within(inferred).getAllByText(/evidence$/)).toHaveLength(
      detail.diagnosis!.candidates.length,
    );
    expect(within(observed).queryByText(/hypothes/i)).not.toBeInTheDocument();
  });

  it("states when the trace is unavailable", () => {
    render(
      <TraceExplorer
        detail={{ ...detail, trace: null, trace_unavailable_reason: "trace purged at 2026-01-01" }}
      />,
    );
    expect(screen.getByText("Trace unavailable")).toBeInTheDocument();
    expect(screen.getByText("trace purged at 2026-01-01")).toBeInTheDocument();
  });
});

describe("ComparisonView", () => {
  it("shows the failed policy, case-level regressions, and side-by-side metrics", async () => {
    const user = userEvent.setup();
    render(<ComparisonView data={fixtures.comparison} />);
    expect(screen.getAllByRole("status")[0]).toHaveTextContent("Regression policy failed");

    const cases = screen.getByRole("table", { name: "Case-level comparison" });
    const newlyFailing = within(cases)
      .getAllByText("newly failing")
      .map((badge) => badge.closest("tr")!.querySelector("td")!.textContent);
    expect(newlyFailing.sort()).toEqual(["search-malformed-once", "search-timeout-once"]);
    expect(within(cases).queryByText("unchanged")).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /^All/ }));
    expect(within(cases).getAllByText("unchanged").length).toBe(10);

    const metrics = screen.getByRole("table", { name: "Metrics comparison" });
    expect(
      within(metrics).getByRole("rowheader", { name: "matched task success rate" }),
    ).toBeInTheDocument();
    expect(within(metrics).getAllByText("not significant").length).toBeGreaterThan(0);
    const p95 = within(metrics).getByRole("rowheader", { name: "latency p95" }).closest("tr")!;
    expect(p95).toHaveTextContent("Not available");
  });
});

describe("review forms and history", () => {
  it("shows correction history oldest first, keeping the original", () => {
    const chains = chainsByReviewer(fixtures.runReviews);
    expect(chains).toHaveLength(1);
    expect(chains[0]!.history.map((r) => r.label)).toEqual(["fail", "pass"]);
    render(
      <ReviewView
        detail={fixtures.runFailed}
        reviews={fixtures.runReviews}
        submitAction={vi.fn()}
        correctActions={{ [chains[0]!.current.id]: vi.fn() }}
      />,
    );
    expect(screen.getByText("History (2 records)")).toBeInTheDocument();
    // Shown on the current record and again in the history list.
    expect(screen.getAllByText("Correction: misread the answer")).toHaveLength(2);
  });

  it("submits through the action and shows field errors it returns", async () => {
    const user = userEvent.setup();
    const action = vi.fn().mockResolvedValue({
      status: "error",
      message: "Please fix the highlighted fields.",
      errors: { rationale: "Explain why the case is ambiguous or insufficiently specified." },
    });
    render(<SubmitReviewForm action={action} />);
    await user.type(screen.getByLabelText("Reviewer name"), "alice");
    await user.click(screen.getByRole("radio", { name: "ambiguous" }));
    await user.click(screen.getByRole("button", { name: "Submit review" }));

    expect(action).toHaveBeenCalledTimes(1);
    const formData = action.mock.calls[0]![1] as FormData;
    expect(formData.get("reviewer")).toBe("alice");
    expect(formData.get("label")).toBe("ambiguous");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please fix the highlighted fields.",
    );
    expect(screen.getByLabelText(/Rationale/)).toHaveAttribute("aria-invalid", "true");
  });

  it("prefills a correction from the current review", () => {
    const current = fixtures.runReviews.current[0]!;
    render(<CorrectReviewForm review={current} action={vi.fn()} />);
    expect(screen.getByRole("radio", { name: current.label })).toBeChecked();
    expect(screen.getByLabelText("Reason for the correction")).toBeRequired();
  });
});

describe("rejected submissions keep the reviewer's input", () => {
  it("restores typed values after the action returns errors", async () => {
    const user = userEvent.setup();
    const action = vi.fn(async (_previous: unknown, formData: FormData) => ({
      status: "error" as const,
      message: "Please fix the highlighted fields.",
      errors: { reason: "Say why you are correcting the review." },
      values: Object.fromEntries(
        [...formData.entries()].filter(
          (entry): entry is [string, string] => typeof entry[1] === "string",
        ),
      ),
    }));
    const current = fixtures.runReviews.current[0]!;
    render(<CorrectReviewForm review={current} action={action} />);
    await user.type(screen.getByLabelText("Reason for the correction"), "typo");
    await user.click(screen.getByRole("radio", { name: "ambiguous" }));
    await user.type(screen.getByLabelText(/Rationale/), "unclear expectation");
    await user.selectOptions(screen.getByLabelText("security violation"), "present");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please fix the highlighted fields.",
    );
    expect(screen.getByLabelText("Reason for the correction")).toHaveValue("typo");
    expect(screen.getByRole("radio", { name: "ambiguous" })).toBeChecked();
    expect(screen.getByLabelText(/Rationale/)).toHaveValue("unclear expectation");
    // React's reset does not restore <select> values on its own; formKey remounts the form.
    expect(screen.getByLabelText("security violation")).toHaveValue("present");
  });
});
