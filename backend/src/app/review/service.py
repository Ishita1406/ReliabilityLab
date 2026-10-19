"""Human review workflow on stored experiments: submit, correct, queue, and report."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.db.benchmarks import load_suite
from app.db.evaluations import load_experiment_results
from app.db.human_reviews import list_human_reviews, load_human_review, save_human_review
from app.domain.timestamps import utc_now
from app.review.agreement import CalibrationReport, calibration_report
from app.review.models import HumanReview, current_reviews, history
from app.review.queue import (
    QueueItem,
    QueuePolicy,
    build_queue,
    sampled_run_ids,
    security_scenarios,
)


class ReviewWorkflowError(ValueError):
    """The requested review action is not allowed."""


class ReviewService:
    def __init__(
        self, sessions: sessionmaker[Session], *, now: Callable[[], datetime] = utc_now
    ) -> None:
        self._sessions = sessions
        self._now = now

    def submit(self, review: HumanReview) -> HumanReview:
        """Record a reviewer's first review of a run (corrections go through ``correct``)."""
        if review.supersedes is not None:
            raise ReviewWorkflowError("use correct() to supersede a review")
        review = review.updated(created_at=self._now())
        with self._sessions.begin() as session:
            save_human_review(session, review)
        return review

    def correct(self, review_id: str, *, reason: str, **changes: object) -> HumanReview:
        """Append a correction of the reviewer's current review; the original is kept."""
        with self._sessions.begin() as session:
            previous = load_human_review(session, review_id)
            chain = [
                r
                for r in list_human_reviews(session, run_id=previous.run_id)
                if r.chain_key == previous.chain_key
            ]
            (head,) = [r for r in current_reviews(chain)]
            if head.id != previous.id:
                raise ReviewWorkflowError(
                    f"review '{review_id}' was already corrected by '{head.id}'; correct that one"
                )
            forbidden = {"id", "run_id", "case_id", "scenario_id", "evaluator_version", "reviewer"}
            if forbidden & set(changes):
                raise ReviewWorkflowError("a correction cannot change who or what was reviewed")
            correction = HumanReview.model_validate(
                {
                    **previous.model_dump(exclude={"id", "created_at"}),
                    **changes,
                    "supersedes": previous.id,
                    "correction_reason": reason,
                    "created_at": self._now(),
                }
            )
            save_human_review(session, correction)
        return correction

    def history(self, review_id: str) -> list[HumanReview]:
        """The full correction chain of the reviewer who wrote ``review_id``, oldest first."""
        with self._sessions() as session:
            review = load_human_review(session, review_id)
            chain = [
                r
                for r in list_human_reviews(session, run_id=review.run_id)
                if r.chain_key == review.chain_key
            ]
        (head,) = current_reviews(chain)
        return history(chain, head)

    def queue(self, experiment_id: str, policy: QueuePolicy | None = None) -> list[QueueItem]:
        with self._sessions() as session:
            results = load_experiment_results(session, experiment_id)
            reviews = list_human_reviews(session, experiment_id=experiment_id)
            config = results.configuration
            adversarial = (
                security_scenarios(load_suite(session, config.suite_id, config.suite_version))
                if config is not None
                else set()
            )
        return build_queue(results, reviews, policy, adversarial_scenarios=adversarial)

    def report(
        self,
        experiment_id: str,
        *,
        scope: Literal["sampled", "all"] = "sampled",
        policy: QueuePolicy | None = None,
        require_blind: bool = True,
    ) -> CalibrationReport:
        """Calibration of the experiment's evaluator against human reviews.

        ``sampled`` (default) restricts to the random calibration sample, the only population
        whose error rates generalise to the evaluator's overall behaviour."""
        policy = policy or QueuePolicy()
        with self._sessions() as session:
            results = load_experiment_results(session, experiment_id)
            reviews = list_human_reviews(session, experiment_id=experiment_id)
        if results.configuration is None:
            raise ReviewWorkflowError(f"experiment '{experiment_id}' has no evaluator snapshot")
        evaluations = {r.run_id: r.evaluation for r in results.runs if r.evaluation is not None}
        return calibration_report(
            evaluations,
            reviews,
            evaluator_version=results.configuration.evaluator_version,
            run_ids=sampled_run_ids(results, policy.sampling) if scope == "sampled" else None,
            scope=scope,
            require_blind=require_blind,
        )
