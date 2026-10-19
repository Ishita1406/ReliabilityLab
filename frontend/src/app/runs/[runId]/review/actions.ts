"use server";

import { refresh } from "next/cache";

import { api } from "@/lib/api/client";
import {
  type FormState,
  parseCorrection,
  parseSubmission,
  submittedValues,
} from "@/lib/review-form";

// No authentication: the reviewer name is self-declared (see docs/engineering/09).

export async function submitReviewAction(
  runId: string,
  _previous: FormState,
  formData: FormData,
): Promise<FormState> {
  const parsed = parseSubmission(formData);
  if (!parsed.ok)
    return {
      status: "error",
      message: "Please fix the highlighted fields.",
      errors: parsed.errors,
      values: submittedValues(formData),
    };
  const result = await api.submitReview(runId, parsed.value);
  if (!result.ok) {
    return {
      status: "error",
      message: result.error.message,
      errors: {},
      values: submittedValues(formData),
    };
  }
  refresh();
  return { status: "success", message: `Review ${result.data.id} saved.`, errors: {} };
}

export async function correctReviewAction(
  reviewId: string,
  _previous: FormState,
  formData: FormData,
): Promise<FormState> {
  const parsed = parseCorrection(formData);
  if (!parsed.ok)
    return {
      status: "error",
      message: "Please fix the highlighted fields.",
      errors: parsed.errors,
      values: submittedValues(formData),
    };
  const result = await api.correctReview(reviewId, parsed.value);
  if (!result.ok) {
    return {
      status: "error",
      message: result.error.message,
      errors: {},
      values: submittedValues(formData),
    };
  }
  refresh();
  return {
    status: "success",
    message: `Correction ${result.data.id} saved; the original review is kept in the history.`,
    errors: {},
  };
}
