/**
 * Named aliases for the generated API schema (`schema.d.ts`, from the backend's OpenAPI file).
 * Regenerate with `npm run api:types` after backend API changes; `npm run api:types:check`
 * fails when the generated file is stale.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Overview = Schemas["Overview"];
export type ExperimentHeadline = Schemas["ExperimentHeadline"];
export type ExperimentSummary = Schemas["ExperimentSummary"];
export type ExperimentDetail = Schemas["ExperimentDetail"];
export type ExperimentConfiguration = Schemas["ExperimentConfiguration"];
export type ExperimentMetrics = Schemas["ExperimentMetrics"];
export type MetricValue = Schemas["MetricValue"];
export type RunSummary = Schemas["RunSummary"];
export type RunDetail = Schemas["RunDetail"];
export type TraceEvent = Schemas["TraceEventRecord"];
export type CaseEvaluation = Schemas["CaseEvaluation"];
export type CriterionEvaluation = Schemas["CriterionEvaluation"];
export type Evidence = Schemas["Evidence"];
export type Diagnosis = Schemas["Diagnosis"];
export type Observation = Schemas["Observation"];
export type CandidateCause = Schemas["CandidateCause"];
export type SuiteSummary = Schemas["SuiteSummary"];
export type BenchmarkSuite = Schemas["BenchmarkSuite"];
export type BenchmarkCase = Schemas["BenchmarkCase"];
export type Scenario = Schemas["Scenario"];
export type ComparisonResponse = Schemas["ComparisonResponse"];
export type ExperimentComparison = Schemas["ExperimentComparison"];
export type UnitComparison = Schemas["UnitComparison"];
export type MetricComparison = Schemas["MetricComparison"];
export type RuleResult = Schemas["RuleResult"];
export type RunReviews = Schemas["RunReviews"];
export type HumanReview = Schemas["HumanReview"];
export type ReviewSubmission = Schemas["ReviewSubmission"];
export type ReviewCorrection = Schemas["ReviewCorrection"];
export type QueueItem = Schemas["QueueItem"];
export type CalibrationReport = Schemas["CalibrationReport"];

export type Verdict = Schemas["Verdict"];
export type RunStatus = Schemas["RunStatus"];
export type ExperimentStatus = Schemas["ExperimentStatus"];
export type FailureLabel = Schemas["FailureLabel"];
export type ReviewLabel = Schemas["ReviewLabel"];
export type CategoryVerdict = Schemas["CategoryVerdict"];
export type EventKind = Schemas["EventKind"];
export type EventStatus = Schemas["EventStatus"];
