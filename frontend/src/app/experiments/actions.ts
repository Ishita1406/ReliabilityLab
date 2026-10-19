"use server";

import { refresh } from "next/cache";
import { redirect } from "next/navigation";

import { api } from "@/lib/api/client";

// No authentication (see docs/engineering/09): anyone who can reach the dashboard can do this.

export interface ActionState {
  status: "idle" | "error" | "success";
  message: string;
  values?: Record<string, string>;
}

function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function values(formData: FormData): Record<string, string> {
  return Object.fromEntries(
    [...formData.entries()].filter(
      (entry): entry is [string, string] =>
        typeof entry[1] === "string" && !entry[0].startsWith("$ACTION"),
    ),
  );
}

export async function createExperimentAction(
  _previous: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const [suiteId, suiteVersion] = text(formData, "suite").split("@");
  const name = text(formData, "name");
  const agentVersionId = text(formData, "agent_version_id");
  const repetitions = Number(text(formData, "repetitions") || "1");
  const maxToolCalls = text(formData, "max_tool_calls");
  const key = text(formData, "idempotency_key");
  if (!name || !suiteId || !suiteVersion || !agentVersionId || !key) {
    return {
      status: "error",
      message: "Name, suite, and agent version are required.",
      values: values(formData),
    };
  }
  if (!Number.isInteger(repetitions) || repetitions < 1) {
    return {
      status: "error",
      message: "Repetitions must be a whole number ≥ 1.",
      values: values(formData),
    };
  }
  const result = await api.createExperiment(
    {
      name,
      description: null,
      suite_id: suiteId,
      suite_version: suiteVersion,
      scenario_ids: null,
      agent_version_id: agentVersionId,
      repetitions,
      seed: 0,
      limits: maxToolCalls
        ? { max_steps: null, max_tool_calls: Number(maxToolCalls), deadline_seconds: null }
        : null,
      submit: true,
    },
    key,
  );
  if (!result.ok) {
    // A 503 still created the experiment; its ID is in the error detail and the list.
    return { status: "error", message: result.error.message, values: values(formData) };
  }
  redirect(`/experiments/${result.data.experiment_id}`);
}

export async function submitExperimentAction(experimentId: string): Promise<ActionState> {
  const result = await api.submitExperiment(experimentId);
  if (!result.ok) return { status: "error", message: result.error.message };
  refresh();
  return { status: "success", message: `Experiment is ${result.data.status}.` };
}

export async function cancelExperimentAction(experimentId: string): Promise<ActionState> {
  const result = await api.cancelExperiment(experimentId);
  if (!result.ok) return { status: "error", message: result.error.message };
  refresh();
  return {
    status: "success",
    message:
      result.data.cancel_requested && result.data.status === "running"
        ? "Cancellation requested; the worker stops after the current run."
        : `Experiment is ${result.data.status}.`,
  };
}
