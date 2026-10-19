/** Typed access to API responses captured from a seeded backend (see scripts/capture-fixtures.mjs). */
import comparison from "./fixtures/comparison.json";
import experimentPending from "./fixtures/experiment-pending.json";
import experiment from "./fixtures/experiment.json";
import overview from "./fixtures/overview.json";
import reviewQueue from "./fixtures/review-queue.json";
import runFailed from "./fixtures/run-failed.json";
import runReviews from "./fixtures/run-reviews.json";
import suite from "./fixtures/suite.json";

import type {
  BenchmarkSuite,
  ComparisonResponse,
  ExperimentDetail,
  Overview,
  QueueItem,
  RunDetail,
  RunReviews,
} from "@/lib/api/types";

// JSON imports widen literal types (e.g. enums to string), so they are asserted to the API
// types here; the fixtures themselves are unmodified API output.
export const fixtures = {
  overview: overview as unknown as Overview,
  suite: suite as unknown as BenchmarkSuite,
  experiment: experiment as unknown as ExperimentDetail,
  experimentPending: experimentPending as unknown as ExperimentDetail,
  runFailed: runFailed as unknown as RunDetail,
  comparison: comparison as unknown as ComparisonResponse,
  runReviews: runReviews as unknown as RunReviews,
  reviewQueue: reviewQueue as unknown as QueueItem[],
};
