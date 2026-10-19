"""The bundled sample dataset and the committed JSON Schema contract."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.benchmarks.loader import (
    DatasetError,
    bundled_suite_path,
    load_bundled_suite,
    load_suite,
)
from app.domain import schema as schema_module
from app.domain.enums import ScenarioKind
from app.domain.schema import SCHEMA_MODELS, render_schema
from tests.domain.factories import case_dict

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"
SAMPLE_ID, SAMPLE_VERSION = "research-agent-sample", "1.0.0"


def _validator(filename: str) -> Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / filename).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


# --- sample dataset ------------------------------------------------------------------------


def test_sample_dataset_loads_and_is_marked_synthetic() -> None:
    suite = load_bundled_suite(SAMPLE_ID, SAMPLE_VERSION)

    assert (suite.id, suite.version) == (SAMPLE_ID, SAMPLE_VERSION)
    assert suite.synthetic
    assert "SYNTHETIC" in suite.description
    assert all(case.metadata.synthetic for case in suite.cases)


def test_sample_dataset_covers_normal_and_failure_oriented_cases() -> None:
    suite = load_bundled_suite(SAMPLE_ID, SAMPLE_VERSION)
    tags = [set(case.tags) for case in suite.cases]

    assert sum("normal" in t for t in tags) >= 2
    assert sum("failure-oriented" in t for t in tags) >= 3
    for case in suite.cases:
        kinds = [s.kind for s in suite.scenarios_for(case.id)]
        assert kinds.count(ScenarioKind.BASELINE) == 1
    assert sum(s.kind is ScenarioKind.FAULT_INJECTED for s in suite.scenarios) >= 5


def test_sample_dataset_round_trips_without_loss() -> None:
    suite = load_bundled_suite(SAMPLE_ID, SAMPLE_VERSION)

    assert type(suite).model_validate_json(suite.model_dump_json()) == suite


def test_sample_override_changes_the_expected_outcome() -> None:
    suite = load_bundled_suite(SAMPLE_ID, SAMPLE_VERSION)

    baseline = suite.effective_expectation("research.fact-lookup.baseline")
    outage = suite.effective_expectation("research.fact-lookup.search-unavailable")

    assert baseline.expected_behavior.outcome == "answered"
    assert outage.expected_behavior.outcome == "task_failed"


def test_loader_rejects_file_stored_under_wrong_version(tmp_path: Path) -> None:
    target = tmp_path / SAMPLE_ID / "9.9.9.json"
    target.parent.mkdir()
    shutil.copy(bundled_suite_path(SAMPLE_ID, SAMPLE_VERSION), target)

    with pytest.raises(DatasetError, match="must be stored as"):
        load_suite(target)


def test_loader_reports_invalid_and_missing_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "1.0"}', encoding="utf-8")

    with pytest.raises(DatasetError, match="invalid dataset"):
        load_suite(bad)
    with pytest.raises(DatasetError, match="cannot read dataset"):
        load_suite(tmp_path / "missing.json")


# --- JSON Schema contract ------------------------------------------------------------------


@pytest.mark.parametrize("filename", sorted(SCHEMA_MODELS))
def test_committed_schema_matches_models(filename: str) -> None:
    committed = (SCHEMAS_DIR / filename).read_text(encoding="utf-8")

    assert committed == render_schema(SCHEMA_MODELS[filename]), (
        "Schema drift: run `uv run python -m app.domain.schema` and commit the result."
    )


def test_schema_check_command_detects_drift(tmp_path: Path) -> None:
    assert schema_module.main(["--output-dir", str(tmp_path)]) == 0
    assert schema_module.main(["--output-dir", str(tmp_path), "--check"]) == 0

    (tmp_path / "benchmark-case.schema.json").write_text("{}", encoding="utf-8")

    assert schema_module.main(["--output-dir", str(tmp_path), "--check"]) == 1


def test_sample_dataset_validates_against_json_schema() -> None:
    raw = json.loads(bundled_suite_path(SAMPLE_ID, SAMPLE_VERSION).read_text(encoding="utf-8"))

    _validator("benchmark-suite.schema.json").validate(raw)
    case_validator = _validator("benchmark-case.schema.json")
    for case in raw["cases"]:
        case_validator.validate(case)


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ({"difficulty": "impossible"}, ["difficulty"]),
        ({"id": "Bad Id"}, ["id"]),
        ({"surprise": True}, []),
    ],
)
def test_json_schema_rejects_invalid_cases(
    mutation: dict[str, Any], expected_path: list[str]
) -> None:
    errors = list(_validator("benchmark-case.schema.json").iter_errors(case_dict(**mutation)))

    assert errors
    assert [list(e.absolute_path) for e in errors] == [expected_path]


def test_json_schema_requires_case_fields() -> None:
    data = case_dict()
    del data["task_description"]

    errors = list(_validator("benchmark-case.schema.json").iter_errors(data))

    assert [e.message for e in errors] == ["'task_description' is a required property"]
