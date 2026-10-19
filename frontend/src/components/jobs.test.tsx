import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import type { JobStatusView } from "@/lib/api/types";

import { AutoRefresh } from "./auto-refresh";
import { JobPanel } from "./job-panel";
import { NewExperimentForm } from "./new-experiment-form";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function job(overrides: Partial<JobStatusView> = {}): JobStatusView {
  return {
    experiment_id: "exp_1",
    status: "queued",
    terminal: false,
    attempts: 0,
    max_attempts: 3,
    queued_at: "2026-10-19T10:00:00Z",
    started_at: null,
    finished_at: null,
    heartbeat_at: null,
    lease_expires_at: null,
    cancel_requested: false,
    last_error_type: null,
    last_error_message: null,
    progress: { planned_runs: 12, stored_runs: 0, evaluated_runs: 0 },
    poll_after_seconds: 2,
    ...overrides,
  };
}

const noop = vi.fn().mockResolvedValue({ status: "idle", message: "" });

describe("AutoRefresh (polling)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    refresh.mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("refreshes on an interval while active", () => {
    render(<AutoRefresh active intervalSeconds={2} label="Live status" />);
    expect(screen.getByText("Live status: refreshing every 2 s")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(6_000));
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it("does nothing once inactive (finished jobs stop polling)", () => {
    const { container } = render(<AutoRefresh active={false} label="Live status" />);
    act(() => void vi.advanceTimersByTime(10_000));
    expect(refresh).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });

  it("skips refreshes while the tab is hidden", () => {
    render(<AutoRefresh active intervalSeconds={1} label="Live status" />);
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    act(() => void vi.advanceTimersByTime(5_000));
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByText("Live status: paused while this tab is hidden")).toBeInTheDocument();
  });
});

describe("JobPanel", () => {
  it("explains a queued job waiting for a worker and offers cancellation", () => {
    render(<JobPanel job={job()} submitAction={noop} cancelAction={noop} />);
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for a worker/)).toBeInTheDocument();
    expect(screen.getByText("0 of 12 runs stored · 0 evaluated")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit for execution" })).not.toBeInTheDocument();
    expect(screen.getByText(/refreshing every 2 s/)).toBeInTheDocument();
  });

  it("shows a pending retry with the last error", () => {
    render(
      <JobPanel
        job={job({
          attempts: 1,
          last_error_type: "OperationalError",
          last_error_message: "connection lost",
          started_at: "2026-10-19T10:00:01Z",
        })}
        submitAction={noop}
        cancelAction={noop}
      />,
    );
    expect(screen.getByText("Waiting to retry after attempt 1 of 3.")).toBeInTheDocument();
    expect(screen.getByText("OperationalError: connection lost")).toBeInTheDocument();
  });

  it("shows a requested cancellation on a running job without a second cancel button", () => {
    render(
      <JobPanel
        job={job({ status: "running", attempts: 1, cancel_requested: true })}
        submitAction={noop}
        cancelAction={noop}
      />,
    );
    expect(screen.getByText(/the worker stops after the current run/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("stops polling and hides controls when the job is finished", () => {
    render(
      <JobPanel
        job={job({
          status: "completed",
          terminal: true,
          attempts: 1,
          poll_after_seconds: null,
          finished_at: "2026-10-19T10:01:00Z",
          progress: { planned_runs: 12, stored_runs: 12, evaluated_runs: 12 },
        })}
        submitAction={noop}
        cancelAction={noop}
      />,
    );
    expect(screen.queryByText(/refreshing every/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("submits a pending experiment through the action", async () => {
    const user = userEvent.setup();
    const submit = vi
      .fn()
      .mockResolvedValue({ status: "success", message: "Experiment is queued." });
    render(
      <JobPanel
        job={job({ status: "pending", queued_at: null })}
        submitAction={submit}
        cancelAction={noop}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Submit for execution" }));
    expect(submit).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("status")).toHaveTextContent("Experiment is queued.");
  });
});

describe("NewExperimentForm", () => {
  it("sends the same idempotency key on every submission of one form", async () => {
    const user = userEvent.setup();
    // Like createExperimentAction: on error, return the submitted values so the form keeps them.
    const action = vi.fn(async (_previous: unknown, formData: FormData) => ({
      status: "error" as const,
      message: "job queue unavailable",
      values: Object.fromEntries(
        [...formData.entries()].filter((e): e is [string, string] => typeof e[1] === "string"),
      ),
    }));
    render(
      <NewExperimentForm
        suites={[
          {
            id: "evaluation-samples",
            version: "1.0.0",
            name: "Evaluation samples",
            description: "",
            synthetic: true,
            case_count: 1,
            scenario_count: 12,
            registered_at: "2026-10-19T00:00:00Z",
          },
        ]}
        agentVersions={[
          {
            id: "agv_1",
            agent_name: "deterministic-research",
            version_label: "0.1.0",
            model: "deterministic/regex-extractor",
            created_at: "2026-10-19T00:00:00Z",
          },
        ]}
        action={action}
      />,
    );
    await user.type(screen.getByLabelText("Name"), "nightly");
    await user.selectOptions(screen.getByLabelText("Suite"), "evaluation-samples@1.0.0");
    await user.selectOptions(screen.getByLabelText("Agent version"), "agv_1");
    await user.click(screen.getByRole("button", { name: "Create and queue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("job queue unavailable");
    await user.click(screen.getByRole("button", { name: "Create and queue" }));

    const keys = action.mock.calls.map((call) => (call[1] as FormData).get("idempotency_key"));
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });
});

describe("job API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the Idempotency-Key header when creating", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ experiment_id: "exp_1", status: "queued" }), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const result = await api.createExperiment(
      {
        name: "n",
        description: null,
        suite_id: "s",
        suite_version: "1.0.0",
        scenario_ids: null,
        agent_version_id: "agv_1",
        repetitions: 1,
        seed: 0,
        limits: null,
        submit: true,
      },
      "key-123",
    );
    expect(result.ok).toBe(true);
    const init = fetchMock.mock.calls[0]![1] as RequestInit;
    expect(init.headers).toEqual({
      "idempotency-key": "key-123",
      "content-type": "application/json",
    });
  });
});
