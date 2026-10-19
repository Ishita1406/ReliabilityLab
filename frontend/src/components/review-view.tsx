import type { HumanReview, RunDetail, RunReviews } from "@/lib/api/types";
import { dateTime, humanize, percent, scenarioName } from "@/lib/format";

import { CorrectReviewForm, type ReviewAction, SubmitReviewForm } from "./review-forms";
import { Badge, EmptyState, Missing, Section, StatusBadge, TextLink } from "./ui";

export function chainsByReviewer(
  reviews: RunReviews,
): { current: HumanReview; history: HumanReview[] }[] {
  return reviews.current.map((current) => ({
    current,
    history: reviews.records
      .filter(
        (r) => r.reviewer === current.reviewer && r.evaluator_version === current.evaluator_version,
      )
      .sort((a, b) => a.created_at.localeCompare(b.created_at)),
  }));
}

function ReviewRecord({ review }: { review: HumanReview }) {
  const categories = Object.entries(review.categories);
  return (
    <div className="text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <Badge tone={review.label === "fail" ? "bad" : review.label === "pass" ? "good" : "warn"}>
          {humanize(review.label)}
        </Badge>
        <span className="text-zinc-600 dark:text-zinc-400">{dateTime(review.created_at)}</span>
        {review.blind ? <Badge>blind</Badge> : <Badge tone="warn">saw automated verdict</Badge>}
        {review.provenance === "synthetic" ? <Badge tone="warn">synthetic</Badge> : null}
        {review.confidence !== null ? (
          <span>confidence {percent(review.confidence, 0)}</span>
        ) : null}
      </p>
      {review.correction_reason ? (
        <p className="mt-1">Correction: {review.correction_reason}</p>
      ) : null}
      {review.rationale ? <p className="mt-1">Rationale: {review.rationale}</p> : null}
      {categories.length ? (
        <p className="mt-1">
          {categories.map(([category, verdict]) => `${humanize(category)}: ${verdict}`).join(" · ")}
        </p>
      ) : null}
      {review.evidence.map((item, index) => (
        <p key={index} className="mt-1">
          Evidence: {item.note}{" "}
          {item.event_id ? <code className="text-xs">{item.event_id}</code> : null}
        </p>
      ))}
    </div>
  );
}

export function ReviewView({
  detail,
  reviews,
  submitAction,
  correctActions,
}: {
  detail: RunDetail;
  reviews: RunReviews;
  submitAction: ReviewAction;
  correctActions: Record<string, ReviewAction>;
}) {
  const evaluation = detail.evaluation;
  const notPassed = evaluation?.criteria.filter((c) => c.status !== "passed") ?? [];
  const chains = chainsByReviewer(reviews);
  return (
    <>
      <p className="mb-6 flex flex-wrap gap-3 text-sm">
        <span>
          {detail.run.case_id} / {scenarioName(detail.run.scenario_id, detail.run.case_id)}
        </span>
        <TextLink href={`/runs/${detail.run.run_id}`}>Open trace explorer</TextLink>
      </p>
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        <div>
          <Section
            title="Evaluator judgment"
            description="Automated; treat as a claim to check, not ground truth."
          >
            {evaluation === null ? (
              <Missing reason="no stored evaluation" />
            ) : (
              <>
                <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                  <StatusBadge status={evaluation.verdict} /> score {percent(evaluation.score)} ·{" "}
                  {evaluation.evaluator_version}
                </p>
                <p className="mb-3 text-sm">{evaluation.explanation}</p>
                {notPassed.length === 0 ? (
                  <p className="text-sm">All criteria passed.</p>
                ) : (
                  <ul className="flex flex-col gap-3 text-sm">
                    {notPassed.map((criterion) => (
                      <li
                        key={criterion.criterion_id}
                        className="rounded border border-zinc-200 p-3 dark:border-zinc-800"
                      >
                        <p className="flex flex-wrap items-center gap-2">
                          <code className="text-xs">{criterion.criterion_id}</code>
                          <StatusBadge status={criterion.status} />
                          {criterion.labels.map((label) => (
                            <Badge key={label}>{humanize(label)}</Badge>
                          ))}
                        </p>
                        <p className="mt-1">{criterion.explanation}</p>
                        <ul className="mt-1 list-disc pl-5">
                          {criterion.evidence.map((item, index) => (
                            <li key={index}>
                              {item.statement}
                              {item.excerpt ? <q className="ml-1 text-xs">{item.excerpt}</q> : null}
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Section>
          <Section title="Final answer">
            <p className="text-sm whitespace-pre-wrap">
              {detail.final_answer ?? (
                <Missing reason={detail.error_message ?? "no final answer"} />
              )}
            </p>
          </Section>
        </div>
        <div>
          <Section
            title="Human annotations"
            description="Reviews are append-only: a correction adds a record and keeps the original."
          >
            {chains.length === 0 ? (
              <EmptyState title="No human reviews yet" />
            ) : (
              <ul className="flex flex-col gap-4">
                {chains.map(({ current, history }) => (
                  <li
                    key={current.id}
                    className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800"
                  >
                    <h3 className="font-semibold">{current.reviewer}</h3>
                    <ReviewRecord review={current} />
                    {history.length > 1 ? (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-sm font-medium">
                          History ({history.length} records)
                        </summary>
                        <ol className="mt-2 flex flex-col gap-2 border-l border-zinc-300 pl-3 dark:border-zinc-700">
                          {history.map((record) => (
                            <li key={record.id}>
                              <ReviewRecord review={record} />
                            </li>
                          ))}
                        </ol>
                      </details>
                    ) : null}
                    {correctActions[current.id] ? (
                      <details className="mt-3">
                        <summary className="cursor-pointer text-sm font-medium">
                          Correct this review
                        </summary>
                        <div className="mt-3">
                          <CorrectReviewForm
                            review={current}
                            action={correctActions[current.id]!}
                          />
                        </div>
                      </details>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Add your review">
            <SubmitReviewForm action={submitAction} />
          </Section>
        </div>
      </div>
    </>
  );
}
