/**
 * The single place that talks to the FastAPI backend.
 *
 * Every call resolves to an `ApiResult`: either the typed data or a classified error. Nothing
 * throws for HTTP or network failures, so pages render explicit error states instead of
 * crashing, and missing data is never silently replaced.
 *
 * Calls run on the server (Server Components and Server Actions); the backend URL comes from
 * `RELIABILITYLAB_API_URL` and is never sent to the browser.
 */
import type {
  CalibrationReport,
  ComparisonResponse,
  ExperimentDetail,
  ExperimentSummary,
  HumanReview,
  Overview,
  QueueItem,
  ReviewCorrection,
  ReviewSubmission,
  RunDetail,
  RunReviews,
  BenchmarkSuite,
  SuiteSummary,
} from "./types";

export type ApiErrorKind = "network" | "not_found" | "http" | "invalid_response";

export interface ApiError {
  kind: ApiErrorKind;
  status: number | null;
  message: string;
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export const DEFAULT_API_URL = "http://127.0.0.1:8000";

export function apiBaseUrl(): string {
  return (process.env.RELIABILITYLAB_API_URL ?? DEFAULT_API_URL).replace(/\/$/, "");
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item ? String(item.msg) : String(item),
        )
        .join("; ");
    }
  }
  return null;
}

export async function request<T>(
  path: string,
  init?: { method?: "GET" | "POST"; body?: unknown; query?: Record<string, string> },
): Promise<ApiResult<T>> {
  const url = new URL(apiBaseUrl() + path);
  for (const [key, value] of Object.entries(init?.query ?? {})) url.searchParams.set(key, value);
  let response: Response;
  try {
    const options: RequestInit = { method: init?.method ?? "GET", cache: "no-store" };
    if (init?.body !== undefined) {
      options.headers = { "content-type": "application/json" };
      options.body = JSON.stringify(init.body);
    }
    response = await fetch(url, options);
  } catch (cause) {
    return {
      ok: false,
      error: {
        kind: "network",
        status: null,
        message: `Could not reach the API at ${apiBaseUrl()}: ${
          cause instanceof Error ? cause.message : String(cause)
        }`,
      },
    };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      ok: false,
      error: {
        kind: "invalid_response",
        status: response.status,
        message: `The API returned a non-JSON response (HTTP ${response.status}).`,
      },
    };
  }
  if (!response.ok) {
    return {
      ok: false,
      error: {
        kind: response.status === 404 ? "not_found" : "http",
        status: response.status,
        message: detailOf(body) ?? `HTTP ${response.status}`,
      },
    };
  }
  return { ok: true, data: body as T };
}

const segment = encodeURIComponent;

export const api = {
  overview: () => request<Overview>("/api/overview"),
  suites: () => request<SuiteSummary[]>("/api/suites"),
  suite: (id: string, version: string) =>
    request<BenchmarkSuite>(`/api/suites/${segment(id)}/${segment(version)}`),
  experiments: () => request<ExperimentSummary[]>("/api/experiments"),
  experiment: (id: string) => request<ExperimentDetail>(`/api/experiments/${segment(id)}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${segment(id)}`),
  comparison: (baseline: string, candidate: string) =>
    request<ComparisonResponse>("/api/comparisons", { query: { baseline, candidate } }),
  reviewQueue: (experimentId: string) =>
    request<QueueItem[]>(`/api/experiments/${segment(experimentId)}/review-queue`),
  calibration: (experimentId: string, scope: "sampled" | "all") =>
    request<CalibrationReport>(`/api/experiments/${segment(experimentId)}/calibration`, {
      query: { scope },
    }),
  runReviews: (runId: string) => request<RunReviews>(`/api/runs/${segment(runId)}/reviews`),
  submitReview: (runId: string, body: ReviewSubmission) =>
    request<HumanReview>(`/api/runs/${segment(runId)}/reviews`, { method: "POST", body }),
  correctReview: (reviewId: string, body: ReviewCorrection) =>
    request<HumanReview>(`/api/reviews/${segment(reviewId)}/corrections`, {
      method: "POST",
      body,
    }),
};
