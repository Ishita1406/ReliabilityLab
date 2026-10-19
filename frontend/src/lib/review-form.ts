/** Parsing and validation of the review forms (shared by server actions and tests). */
import type { CategoryVerdict, FailureLabel, ReviewLabel } from "./api/types";

export const REVIEW_LABELS: ReviewLabel[] = [
  "pass",
  "fail",
  "ambiguous",
  "insufficiently_specified",
];

export const REVIEWABLE_CATEGORIES: FailureLabel[] = [
  "task_correctness",
  "evidence_handling",
  "context_retention",
  "tool_failure",
  "security_violation",
  "execution_limit_violation",
  "invalid_output",
  "run_error",
];

const CATEGORY_VERDICTS: CategoryVerdict[] = ["present", "absent", "unsure"];

export interface ReviewFields {
  label: ReviewLabel;
  categories: Partial<Record<FailureLabel, CategoryVerdict>>;
  confidence: number | null;
  rationale: string | null;
  evidence: { note: string; event_id: string | null; excerpt: null }[];
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; errors: Record<string, string> };

function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function parseReviewFields(formData: FormData): Parsed<ReviewFields> {
  const errors: Record<string, string> = {};
  const label = text(formData, "label") as ReviewLabel;
  if (!REVIEW_LABELS.includes(label)) errors.label = "Choose a label.";
  const rationale = text(formData, "rationale") || null;
  if ((label === "ambiguous" || label === "insufficiently_specified") && !rationale) {
    errors.rationale = "Explain why the case is ambiguous or insufficiently specified.";
  }
  let confidence: number | null = null;
  const rawConfidence = text(formData, "confidence");
  if (rawConfidence) {
    const parsed = Number(rawConfidence);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
      errors.confidence = "Confidence must be between 0 and 100.";
    } else {
      confidence = parsed / 100;
    }
  }
  const categories: ReviewFields["categories"] = {};
  for (const category of REVIEWABLE_CATEGORIES) {
    const verdict = text(formData, `category.${category}`) as CategoryVerdict;
    if (CATEGORY_VERDICTS.includes(verdict)) categories[category] = verdict;
  }
  const note = text(formData, "evidence_note");
  const eventId = text(formData, "evidence_event_id") || null;
  if (eventId && !/^evt_[0-9a-f]{32}$/.test(eventId))
    errors.evidence_event_id = "Not a trace event ID.";
  if (eventId && !note) errors.evidence_note = "Add a note describing the evidence.";
  if (Object.keys(errors).length) return { ok: false, errors };
  return {
    ok: true,
    value: {
      label,
      categories,
      confidence,
      rationale,
      evidence: note ? [{ note, event_id: eventId, excerpt: null }] : [],
    },
  };
}

export function parseSubmission(
  formData: FormData,
): Parsed<ReviewFields & { reviewer: string; blind: boolean }> {
  const fields = parseReviewFields(formData);
  const reviewer = text(formData, "reviewer");
  const errors = fields.ok ? {} : { ...fields.errors };
  if (!reviewer) errors.reviewer = "Enter your name.";
  if (!fields.ok || Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, value: { ...fields.value, reviewer, blind: formData.get("blind") === "on" } };
}

export function parseCorrection(formData: FormData): Parsed<ReviewFields & { reason: string }> {
  const fields = parseReviewFields(formData);
  const reason = text(formData, "reason");
  const errors = fields.ok ? {} : { ...fields.errors };
  if (!reason) errors.reason = "Say why you are correcting the review.";
  if (!fields.ok || Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, value: { ...fields.value, reason } };
}

export interface FormState {
  status: "idle" | "success" | "error";
  message: string;
  errors: Record<string, string>;
  /** Submitted values, returned on error: React resets uncontrolled forms after an action,
   * and the fields use these as defaults so the reviewer's input is not lost. */
  values?: Record<string, string>;
}

export const IDLE: FormState = { status: "idle", message: "", errors: {} };

export function submittedValues(formData: FormData): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of formData.entries()) {
    if (typeof value === "string" && !key.startsWith("$ACTION")) values[key] = value;
  }
  return values;
}
