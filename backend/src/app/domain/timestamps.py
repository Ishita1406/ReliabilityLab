"""Timestamp convention: timezone-aware, normalised to UTC, serialised as ISO 8601 with ``Z``."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, PlainSerializer


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Format as ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (always microsecond precision)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


UtcDateTime = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
]
"""A datetime that must carry a timezone; stored in UTC. Naive datetimes are rejected."""


def utc_now() -> datetime:
    """Current time as a UTC-aware datetime."""
    return datetime.now(UTC)
