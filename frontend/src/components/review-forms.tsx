"use client";

import { useActionState, useId } from "react";

import type { HumanReview } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { type FormState, IDLE, REVIEWABLE_CATEGORIES, REVIEW_LABELS } from "@/lib/review-form";

export type ReviewAction = (previous: FormState, formData: FormData) => Promise<FormState>;

const field =
  "rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <span id={id} className="text-xs text-red-700 dark:text-red-400">
      {message}
    </span>
  );
}

/** Value to show after a rejected submission, else the given default. */
function kept(state: FormState, name: string, fallback: string): string {
  return state.values?.[name] ?? fallback;
}

function errorProps(state: FormState, name: string, id: string) {
  const message = state.errors[name];
  return message ? { "aria-invalid": true, "aria-describedby": `${id}-${name}-error` } : {};
}

function SharedFields({
  state,
  id,
  defaults,
}: {
  state: FormState;
  id: string;
  defaults?: HumanReview;
}) {
  return (
    <>
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">Label</legend>
        <div className="flex flex-wrap gap-3 text-sm">
          {REVIEW_LABELS.map((label) => (
            <label key={label} className="flex items-center gap-1">
              <input
                type="radio"
                name="label"
                value={label}
                defaultChecked={kept(state, "label", defaults?.label ?? "") === label}
                required
              />
              {humanize(label)}
            </label>
          ))}
        </div>
        <FieldError id={`${id}-label-error`} message={state.errors.label} />
        <span className="text-xs text-zinc-500">
          Ambiguous and insufficiently specified are kept separate from pass and fail.
        </span>
      </fieldset>
      <label className="flex flex-col gap-1 text-sm">
        Rationale (required for ambiguous / insufficiently specified)
        <textarea
          name="rationale"
          rows={2}
          className={field}
          defaultValue={kept(state, "rationale", defaults?.rationale ?? "")}
          {...errorProps(state, "rationale", id)}
        />
      </label>
      <FieldError id={`${id}-rationale-error`} message={state.errors.rationale} />
      <label className="flex flex-col gap-1 text-sm">
        Your confidence (0–100, optional)
        <input
          name="confidence"
          type="number"
          min={0}
          max={100}
          className={`${field} w-28`}
          defaultValue={kept(
            state,
            "confidence",
            defaults?.confidence == null ? "" : String(Math.round(defaults.confidence * 100)),
          )}
          {...errorProps(state, "confidence", id)}
        />
      </label>
      <FieldError id={`${id}-confidence-error`} message={state.errors.confidence} />
      <details className="text-sm">
        <summary className="cursor-pointer font-medium">Failure categories (optional)</summary>
        <p className="mt-1 text-xs text-zinc-500">
          Leave “not assessed” for categories you did not check.
        </p>
        <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {REVIEWABLE_CATEGORIES.map((category) => (
            <label key={category} className="flex items-center justify-between gap-2">
              {humanize(category)}
              <select
                name={`category.${category}`}
                className={field}
                defaultValue={kept(
                  state,
                  `category.${category}`,
                  defaults?.categories[category] ?? "",
                )}
              >
                <option value="">not assessed</option>
                <option value="present">present</option>
                <option value="absent">absent</option>
                <option value="unsure">unsure</option>
              </select>
            </label>
          ))}
        </div>
      </details>
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Evidence note (optional)
          <input
            name="evidence_note"
            className={field}
            defaultValue={kept(state, "evidence_note", "")}
            {...errorProps(state, "evidence_note", id)}
          />
        </label>
        <FieldError id={`${id}-evidence_note-error`} message={state.errors.evidence_note} />
        <label className="flex flex-col gap-1 text-sm">
          Trace event ID (optional)
          <input
            name="evidence_event_id"
            className={`${field} font-mono`}
            placeholder="evt_…"
            defaultValue={kept(state, "evidence_event_id", "")}
            {...errorProps(state, "evidence_event_id", id)}
          />
        </label>
        <FieldError id={`${id}-evidence_event_id-error`} message={state.errors.evidence_event_id} />
      </div>
    </>
  );
}

function Feedback({ state }: { state: FormState }) {
  if (state.status === "idle") return null;
  return (
    <p
      role={state.status === "error" ? "alert" : "status"}
      className={`text-sm ${state.status === "error" ? "text-red-700 dark:text-red-400" : "text-emerald-700 dark:text-emerald-400"}`}
    >
      {state.message}
    </p>
  );
}

const button =
  "self-start rounded bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900";

export function SubmitReviewForm({ action }: { action: ReviewAction }) {
  const [state, formAction, pending] = useActionState(action, IDLE);
  const id = useId();
  return (
    <form action={formAction} aria-label="Submit a review" className="flex flex-col gap-4">
      <label className="flex flex-col gap-1 text-sm">
        Reviewer name
        <input
          name="reviewer"
          required
          className={field}
          defaultValue={kept(state, "reviewer", "")}
          autoComplete="name"
          {...errorProps(state, "reviewer", id)}
        />
      </label>
      <FieldError id={`${id}-reviewer-error`} message={state.errors.reviewer} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="blind" defaultChecked={kept(state, "blind", "") === "on"} />I
        decided this label before looking at the automated verdict
      </label>
      <SharedFields state={state} id={id} />
      <button type="submit" className={button} disabled={pending}>
        {pending ? "Saving…" : "Submit review"}
      </button>
      <Feedback state={state} />
    </form>
  );
}

export function CorrectReviewForm({
  review,
  action,
}: {
  review: HumanReview;
  action: ReviewAction;
}) {
  const [state, formAction, pending] = useActionState(action, IDLE);
  const id = useId();
  return (
    <form
      action={formAction}
      aria-label={`Correct ${review.reviewer}'s review`}
      className="flex flex-col gap-4"
    >
      <label className="flex flex-col gap-1 text-sm">
        Reason for the correction
        <input
          name="reason"
          required
          className={field}
          defaultValue={kept(state, "reason", "")}
          {...errorProps(state, "reason", id)}
        />
      </label>
      <FieldError id={`${id}-reason-error`} message={state.errors.reason} />
      <SharedFields state={state} id={id} defaults={review} />
      <button type="submit" className={button} disabled={pending}>
        {pending ? "Saving…" : "Save correction"}
      </button>
      <Feedback state={state} />
    </form>
  );
}
