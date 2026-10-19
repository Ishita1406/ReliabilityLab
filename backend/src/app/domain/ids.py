"""Identifier types.

Two kinds of identifiers are used:

* **Authored slugs** identify hand-written benchmark content (suites, cases, scenarios,
  criteria, faults, tags, tools). They are chosen by dataset authors and stay stable across
  dataset versions, so results for ``research.fact-lookup`` remain comparable over time.
* **Generated IDs** identify runtime entities (agents, experiments, runs, trace events, ...).
  They have the form ``<prefix>_<32 lowercase hex chars>``: a random UUID4 from ``new_id``, or a
  name-based UUID5 from ``derived_id`` when the same inputs must always yield the same ID.
  The prefix is validated, so passing a run ID where an experiment ID is expected fails.
"""

import re
import uuid
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

SLUG_PATTERN = r"^[a-z0-9]+(?:[.\-_][a-z0-9]+)*$"

Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, min_length=1, max_length=100)]
"""Lowercase alphanumerics separated by single ``.``, ``-`` or ``_``."""


class IdPrefix(StrEnum):
    """Prefix for each generated-ID entity type."""

    AGENT_CONFIG = "agt"
    AGENT_VERSION = "agv"
    EXPERIMENT = "exp"
    RUN = "run"
    TRACE_EVENT = "evt"
    TOOL_CALL = "tcl"
    EVALUATION_RESULT = "evr"
    FAILURE_CLASSIFICATION = "fcl"
    REVIEW_ANNOTATION = "rev"
    HUMAN_REVIEW = "hrv"


_HEX32 = "[0-9a-f]{32}"

AgentConfigId = Annotated[str, StringConstraints(pattern=rf"^agt_{_HEX32}$")]
AgentVersionId = Annotated[str, StringConstraints(pattern=rf"^agv_{_HEX32}$")]
ExperimentId = Annotated[str, StringConstraints(pattern=rf"^exp_{_HEX32}$")]
RunId = Annotated[str, StringConstraints(pattern=rf"^run_{_HEX32}$")]
TraceEventId = Annotated[str, StringConstraints(pattern=rf"^evt_{_HEX32}$")]
ToolCallId = Annotated[str, StringConstraints(pattern=rf"^tcl_{_HEX32}$")]
EvaluationResultId = Annotated[str, StringConstraints(pattern=rf"^evr_{_HEX32}$")]
FailureClassificationId = Annotated[str, StringConstraints(pattern=rf"^fcl_{_HEX32}$")]
ReviewAnnotationId = Annotated[str, StringConstraints(pattern=rf"^rev_{_HEX32}$")]
HumanReviewId = Annotated[str, StringConstraints(pattern=rf"^hrv_{_HEX32}$")]


def new_id(prefix: IdPrefix) -> str:
    """Generate a new random identifier for the given entity prefix."""
    return f"{prefix.value}_{uuid.uuid4().hex}"


_DERIVED_ID_NAMESPACE = uuid.UUID("6f0d2c4e-8a59-4c1e-9a43-2f7b1d5e8c90")


def derived_id(prefix: IdPrefix, *parts: str) -> str:
    """Deterministic identifier derived from ``parts`` (e.g. a run ID and a call index).

    Used where re-running the same inputs must produce byte-identical records.
    """
    name = "\x1f".join(parts)
    return f"{prefix.value}_{uuid.uuid5(_DERIVED_ID_NAMESPACE, name).hex}"


def prefix_of(identifier: str) -> IdPrefix | None:
    """Return the prefix of a generated ID, or ``None`` if it is not a generated ID."""
    head, sep, _ = identifier.partition("_")
    if not sep:
        return None
    try:
        return IdPrefix(head)
    except ValueError:
        return None


def is_valid_id(identifier: str, prefix: IdPrefix) -> bool:
    """True if ``identifier`` is a well-formed generated ID with the given prefix."""
    return re.fullmatch(rf"{prefix.value}_{_HEX32}", identifier) is not None
