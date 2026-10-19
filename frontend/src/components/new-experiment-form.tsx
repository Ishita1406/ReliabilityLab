"use client";

import { useActionState, useState } from "react";

import type { AgentVersionSummary, SuiteSummary } from "@/lib/api/types";
import { formKey } from "@/lib/review-form";

export interface CreateState {
  status: "idle" | "error" | "success";
  message: string;
  values?: Record<string, string>;
}

export type CreateAction = (previous: CreateState, formData: FormData) => Promise<CreateState>;

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

/**
 * Creates and queues an experiment. The idempotency key is generated once per form instance,
 * so a double click or a retried submission cannot create two experiments.
 */
export function NewExperimentForm({
  suites,
  agentVersions,
  action,
}: {
  suites: SuiteSummary[];
  agentVersions: AgentVersionSummary[];
  action: CreateAction;
}) {
  const [state, formAction, pending] = useActionState(action, { status: "idle", message: "" });
  const [key] = useState(() => crypto.randomUUID());
  const kept = (name: string, fallback = "") => state.values?.[name] ?? fallback;
  return (
    <form
      key={formKey(state.values)}
      action={formAction}
      aria-label="New experiment"
      className="flex flex-wrap items-end gap-3"
    >
      <input type="hidden" name="idempotency_key" value={key} />
      <label className="flex flex-col gap-1 text-sm">
        Name
        <input name="name" required className={field} defaultValue={kept("name")} />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Suite
        <select name="suite" required className={field} defaultValue={kept("suite")}>
          <option value="">Choose…</option>
          {suites.map((suite) => (
            <option key={`${suite.id}@${suite.version}`} value={`${suite.id}@${suite.version}`}>
              {suite.name} ({suite.version})
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Agent version
        <select
          name="agent_version_id"
          required
          className={field}
          defaultValue={kept("agent_version_id")}
        >
          <option value="">Choose…</option>
          {agentVersions.map((version) => (
            <option key={version.id} value={version.id}>
              {version.agent_name} {version.version_label} ({version.model})
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Repetitions
        <input
          name="repetitions"
          type="number"
          min={1}
          className={`${field} w-24`}
          defaultValue={kept("repetitions", "1")}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        Max tool calls per run (optional)
        <input
          name="max_tool_calls"
          type="number"
          min={1}
          className={`${field} w-28`}
          defaultValue={kept("max_tool_calls")}
        />
      </label>
      <button
        type="submit"
        disabled={pending}
        className="rounded bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
      >
        {pending ? "Queuing…" : "Create and queue"}
      </button>
      {state.status === "error" ? (
        <p role="alert" className="w-full text-sm text-red-700 dark:text-red-400">
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
