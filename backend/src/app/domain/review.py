"""Human review annotations on runs, evaluations, classifications, and trace events."""

from typing import Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.enums import FailureCategory, ReviewTargetType, ReviewVerdict
from app.domain.ids import IdPrefix, ReviewAnnotationId, is_valid_id, new_id
from app.domain.timestamps import UtcDateTime, utc_now

TARGET_PREFIXES: dict[ReviewTargetType, IdPrefix] = {
    ReviewTargetType.RUN: IdPrefix.RUN,
    ReviewTargetType.EVALUATION_RESULT: IdPrefix.EVALUATION_RESULT,
    ReviewTargetType.FAILURE_CLASSIFICATION: IdPrefix.FAILURE_CLASSIFICATION,
    ReviewTargetType.TRACE_EVENT: IdPrefix.TRACE_EVENT,
}


class HumanReviewAnnotation(DomainModel):
    id: ReviewAnnotationId = Field(default_factory=lambda: new_id(IdPrefix.REVIEW_ANNOTATION))
    target_type: ReviewTargetType
    target_id: NonEmptyStr = Field(description="Generated ID of the reviewed entity.")
    reviewer: NonEmptyStr
    verdict: ReviewVerdict
    comment: NonEmptyStr | None = None
    corrected_failure_category: FailureCategory | None = Field(
        default=None, description="Only for failure_classification targets."
    )
    corrected_passed: bool | None = Field(
        default=None, description="Only for evaluation_result targets."
    )
    created_at: UtcDateTime = Field(default_factory=utc_now)
    updated_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        prefix = TARGET_PREFIXES[self.target_type]
        if not is_valid_id(self.target_id, prefix):
            raise ValueError(
                f"target_id must be a '{prefix.value}_' ID for target_type '{self.target_type}'"
            )
        if (
            self.corrected_failure_category is not None
            and self.target_type is not ReviewTargetType.FAILURE_CLASSIFICATION
        ):
            raise ValueError("corrected_failure_category requires a failure_classification target")
        if (
            self.corrected_passed is not None
            and self.target_type is not ReviewTargetType.EVALUATION_RESULT
        ):
            raise ValueError("corrected_passed requires an evaluation_result target")
        if self.verdict is ReviewVerdict.DISAGREE and not (
            self.comment or self.corrected_failure_category or self.corrected_passed is not None
        ):
            raise ValueError("a 'disagree' verdict needs a comment or a correction")
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        return self
