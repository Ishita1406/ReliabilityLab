"""Shared Pydantic base class and constrained primitive types for domain models."""

from collections import Counter
from collections.abc import Iterable
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
SemVer = Annotated[str, StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")]


class DomainModel(BaseModel):
    """Base for every domain entity and value object.

    - ``extra="forbid"``: unknown fields are rejected, so typos in dataset files fail loudly.
    - ``frozen=True``: records are immutable; changes go through ``updated()``, which builds
      and validates a new instance. (``validate_assignment`` was rejected: Pydantic applies the
      new value before running cross-field validators and does not roll it back when they
      fail, leaving an invalid object behind.)
    """

    # json_schema_serialization_defaults_required: in *response* (serialization) schemas, fields
    # with defaults are always present, so generated API clients see them as required. Input
    # and dataset (validation) schemas are unaffected.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=True
    )

    def updated(self, **changes: object) -> Self:
        """Return a fully re-validated copy with ``changes`` applied together.

        Use this for multi-field transitions (e.g. status plus timestamp) that would be invalid
        if assigned one field at a time. Unlike ``model_copy(update=...)``, it validates.
        """
        return self.model_validate({**self.model_dump(), **changes})


def require_unique(values: Iterable[str], label: str) -> None:
    """Raise ``ValueError`` naming any duplicated values."""
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")
