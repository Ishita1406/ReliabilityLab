"use client";

import { useActionState } from "react";

import type { JobStatusView } from "@/lib/api/types";
import { dateTime } from "@/lib/format";

import { AutoRefresh } from "./auto-refresh";
import { DefinitionList, Missing, StatusBadge } from "./ui";

export interface JobActionState {
  status: "idle" | "error" | "success";
  message: string;
}

export type JobAction = (previous: JobActionState) => Promise<JobActionState>;

const IDLE: JobActionState = { status: "idle", message: "" };

const button =
  "rounded border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

function ActionButton({ label, action }: { label: string; action: JobAction }) {
  const [state, formAction, pending] = useActionState(action, IDLE);
  return (
    <form action={formAction} className="flex flex-col gap-1">
      <button type="submit" className={button} disabled={pending}>
        {pending ? `${label}…` : label}
      </button>
      {state.status !== "idle" ? (
        <span role={state.status === "error" ? "alert" : "status"} className="text-xs">
          {state.message}
        </span>
      ) : null}
    </form>
  );
}

function waitingText(job: JobStatusView): string | null {
  if (job.status === "queued") {
    return job.attempts > 0
      ? `Waiting to retry after attempt ${job.attempts} of ${job.max_attempts}.`
      : "Waiting for a worker. If this does not change, check that a worker is running.";
  }
  if (job.status === "running" && job.cancel_requested) {
    return "Cancellation requested; the worker stops after the current run.";
  }
  return null;
}

export function JobPanel({
  job,
  submitAction,
  cancelAction,
}: {
  job: JobStatusView;
  submitAction: JobAction;
  cancelAction: JobAction;
}) {
  const { progress } = job;
  const waiting = waitingText(job);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={job.status} />
        <progress
          className="h-2 w-64 max-w-full"
          value={progress.stored_runs}
          max={Math.max(progress.planned_runs, 1)}
          aria-label="Runs stored"
        />
        <span className="text-sm tabular-nums">
          {progress.stored_runs} of {progress.planned_runs} runs stored · {progress.evaluated_runs}{" "}
          evaluated
        </span>
      </div>
      {waiting ? <p className="text-sm">{waiting}</p> : null}
      <AutoRefresh
        active={!job.terminal}
        intervalSeconds={job.poll_after_seconds ?? 2}
        label="Live status"
      />
      <DefinitionList
        items={[
          ["Attempts", `${job.attempts} of ${job.max_attempts}`],
          ["Queued", dateTime(job.queued_at) ?? <Missing key="q" reason="not submitted" />],
          ["Started", dateTime(job.started_at) ?? <Missing key="s" reason="not started" />],
          ["Finished", dateTime(job.finished_at) ?? <Missing key="f" reason="not finished" />],
          [
            "Last heartbeat",
            dateTime(job.heartbeat_at) ?? <Missing key="h" reason="no worker yet" />,
          ],
          [
            "Last error",
            job.last_error_type ? (
              `${job.last_error_type}: ${job.last_error_message ?? ""}`
            ) : (
              <span key="e">none</span>
            ),
          ],
        ]}
      />
      <div className="flex flex-wrap gap-3">
        {job.status === "pending" ? (
          <ActionButton label="Submit for execution" action={submitAction} />
        ) : null}
        {["pending", "queued", "running"].includes(job.status) && !job.cancel_requested ? (
          <ActionButton label="Cancel" action={cancelAction} />
        ) : null}
      </div>
    </div>
  );
}
