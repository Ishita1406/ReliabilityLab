import type { Metadata } from "next";
import { Suspense } from "react";

import type { ReviewAction } from "@/components/review-forms";
import { ReviewView } from "@/components/review-view";
import { ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api/client";

import { correctReviewAction, submitReviewAction } from "./actions";

export const metadata: Metadata = { title: "Failure review" };

async function ReviewData({ params }: { params: PageProps<"/runs/[runId]/review">["params"] }) {
  const { runId } = await params;
  const [run, reviews] = await Promise.all([api.run(runId), api.runReviews(runId)]);
  if (!run.ok) return <ErrorState error={run.error} title="Could not load the run" />;
  if (!reviews.ok) return <ErrorState error={reviews.error} title="Could not load reviews" />;
  const correctActions: Record<string, ReviewAction> = Object.fromEntries(
    reviews.data.current.map((review) => [review.id, correctReviewAction.bind(null, review.id)]),
  );
  return (
    <ReviewView
      detail={run.data}
      reviews={reviews.data}
      submitAction={submitReviewAction.bind(null, runId)}
      correctActions={correctActions}
    />
  );
}

export default function ReviewPage(props: PageProps<"/runs/[runId]/review">) {
  return (
    <>
      <PageHeader
        title="Failure review"
        description="Compare the evaluator's judgment with the evidence and record a human label."
      />
      <Suspense fallback={<LoadingState label="Loading review…" />}>
        <ReviewData params={props.params} />
      </Suspense>
    </>
  );
}
