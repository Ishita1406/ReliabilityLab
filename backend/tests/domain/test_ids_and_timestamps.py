from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.domain import ids
from app.domain.base import DomainModel
from app.domain.experiment import Run
from app.domain.ids import IdPrefix, Slug, is_valid_id, new_id, prefix_of
from app.domain.timestamps import UtcDateTime, format_utc
from tests.domain.factories import ids as make_ids

ALIAS_BY_PREFIX = {
    IdPrefix.AGENT_CONFIG: ids.AgentConfigId,
    IdPrefix.AGENT_VERSION: ids.AgentVersionId,
    IdPrefix.EXPERIMENT: ids.ExperimentId,
    IdPrefix.RUN: ids.RunId,
    IdPrefix.TRACE_EVENT: ids.TraceEventId,
    IdPrefix.TOOL_CALL: ids.ToolCallId,
    IdPrefix.EVALUATION_RESULT: ids.EvaluationResultId,
    IdPrefix.FAILURE_CLASSIFICATION: ids.FailureClassificationId,
    IdPrefix.REVIEW_ANNOTATION: ids.ReviewAnnotationId,
}


def test_every_prefix_has_a_matching_id_type() -> None:
    assert set(ALIAS_BY_PREFIX) == set(IdPrefix)


@pytest.mark.parametrize("prefix", list(IdPrefix))
def test_generated_ids_validate_against_their_own_type_only(prefix: IdPrefix) -> None:
    identifier = new_id(prefix)

    assert TypeAdapter(ALIAS_BY_PREFIX[prefix]).validate_python(identifier) == identifier
    assert prefix_of(identifier) is prefix
    assert is_valid_id(identifier, prefix)
    for other_prefix, other_alias in ALIAS_BY_PREFIX.items():
        if other_prefix is not prefix:
            with pytest.raises(ValidationError):
                TypeAdapter(other_alias).validate_python(identifier)


def test_generated_ids_are_unique() -> None:
    generated = {new_id(IdPrefix.RUN) for _ in range(10_000)}

    assert len(generated) == 10_000


@pytest.mark.parametrize(
    "value",
    ["run_", "run_XYZ", "run_" + "a" * 31, "run_" + "A" * 32, "RUN_" + "a" * 32, "run-" + "a" * 32],
)
def test_malformed_generated_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ids.RunId).validate_python(value)


def test_prefix_of_returns_none_for_non_generated_ids() -> None:
    assert prefix_of("research.fact-lookup") is None
    assert prefix_of("zzz_" + "a" * 32) is None


@pytest.mark.parametrize(
    "slug", ["a", "research.fact-lookup", "case_01", "v1.2-beta", "research-agent-sample"]
)
def test_valid_slugs(slug: str) -> None:
    assert TypeAdapter(Slug).validate_python(slug) == slug


@pytest.mark.parametrize(
    "slug", ["", "Upper", "has space", ".leading", "trailing-", "double..dot", "a/b", "x" * 101]
)
def test_invalid_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Slug).validate_python(slug)


def test_ids_are_stable_across_json_round_trip() -> None:
    generated = make_ids()
    run = Run(
        experiment_id=generated["experiment_id"],
        case_id="demo.lookup",
        scenario_id="demo.lookup.baseline",
        agent_version_id=generated["agent_version_id"],
        repetition=0,
        seed=7,
    )

    restored = Run.model_validate_json(run.model_dump_json())

    assert restored.id == run.id
    assert restored == run


def test_default_id_factory_only_runs_when_id_is_missing() -> None:
    generated = make_ids()
    data = {
        "experiment_id": generated["experiment_id"],
        "case_id": "c",
        "scenario_id": "s",
        "agent_version_id": generated["agent_version_id"],
        "repetition": 0,
        "seed": 0,
    }

    assert Run.model_validate(data).id != Run.model_validate(data).id
    assert Run.model_validate({**data, "id": generated["run_id"]}).id == generated["run_id"]


class _Stamped(DomainModel):
    at: UtcDateTime


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _Stamped(at=datetime(2026, 1, 1, 12, 0))  # deliberately naive


def test_offset_datetimes_are_normalised_to_utc() -> None:
    plus_five_thirty = timezone(timedelta(hours=5, minutes=30))

    stamped = _Stamped(at=datetime(2026, 1, 1, 17, 30, tzinfo=plus_five_thirty))

    assert stamped.at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert stamped.at.tzinfo is UTC


def test_timestamps_serialise_as_utc_iso8601_with_z_and_microseconds() -> None:
    stamped = _Stamped(at=datetime(2026, 1, 1, 12, 0, 0, 5, tzinfo=UTC))

    assert stamped.model_dump_json() == '{"at":"2026-01-01T12:00:00.000005Z"}'
    assert format_utc(stamped.at) == "2026-01-01T12:00:00.000005Z"
    # Python-mode dumps keep real datetime objects.
    assert stamped.model_dump()["at"] == stamped.at


@pytest.mark.parametrize(
    "raw", ["2026-01-01T12:00:00Z", "2026-01-01T12:00:00+00:00", "2026-01-01T17:30:00+05:30"]
)
def test_timestamp_strings_with_offsets_parse_to_the_same_instant(raw: str) -> None:
    stamped = _Stamped.model_validate_json(f'{{"at": "{raw}"}}')

    assert stamped.at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_timestamp_strings_without_offset_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _Stamped.model_validate_json('{"at": "2026-01-01T12:00:00"}')
