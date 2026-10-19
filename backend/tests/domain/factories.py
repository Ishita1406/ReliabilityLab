"""Builders for minimal valid domain records, as plain dicts so tests can break one field."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.ids import IdPrefix, new_id

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A fixed, deterministic timestamp ``seconds`` after ``T0``."""
    return T0 + timedelta(seconds=seconds)


def case_dict(**overrides: object) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": "demo.lookup",
        "title": "Demo lookup",
        "task_description": "Look up one fact.",
        "inputs": {
            "prompt": "When was the lighthouse completed?",
            "documents": [{"id": "almanac", "title": "Almanac", "content": "Completed in 1873."}],
            "available_tools": ["search_documents"],
        },
        "expectation": {
            "expected_behavior": {
                "outcome": "answered",
                "description": "States 1873.",
                "reference_answer": "1873",
            },
            "evaluation_criteria": [
                {
                    "id": "declares-answered",
                    "type": "declared_outcome",
                    "description": "Declares answered.",
                    "expected": "answered",
                    "required": True,
                },
                {
                    "id": "states-year",
                    "type": "answer_contains",
                    "description": "Contains the year.",
                    "values": ["1873"],
                },
            ],
        },
        "tags": ["demo"],
        "difficulty": "easy",
        "metadata": {"synthetic": True, "provenance": "unit test"},
    }
    case.update(overrides)
    return case


def baseline_scenario_dict(case_id: str = "demo.lookup", **overrides: object) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "id": f"{case_id}.baseline",
        "case_id": case_id,
        "kind": "baseline",
        "description": "Normal tools.",
    }
    scenario.update(overrides)
    return scenario


def fault_scenario_dict(case_id: str = "demo.lookup", **overrides: object) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "id": f"{case_id}.timeout",
        "case_id": case_id,
        "kind": "fault_injected",
        "description": "First search times out.",
        "faults": [
            {
                "id": "first-timeout",
                "type": "tool_timeout",
                "target_tool": "search_documents",
                "apply_on_calls": [1],
                "timeout_ms": 1000,
            }
        ],
    }
    scenario.update(overrides)
    return scenario


def suite_dict(**overrides: object) -> dict[str, Any]:
    suite: dict[str, Any] = {
        "schema_version": "1.0",
        "id": "demo-suite",
        "version": "1.0.0",
        "name": "Demo suite",
        "description": "Synthetic suite for unit tests.",
        "synthetic": True,
        "created_at": "2026-01-01T00:00:00Z",
        "cases": [case_dict()],
        "scenarios": [baseline_scenario_dict(), fault_scenario_dict()],
    }
    suite.update(overrides)
    return suite


def ids() -> dict[str, str]:
    """A fresh, consistent set of generated IDs for one run."""
    return {
        "agent_config_id": new_id(IdPrefix.AGENT_CONFIG),
        "agent_version_id": new_id(IdPrefix.AGENT_VERSION),
        "experiment_id": new_id(IdPrefix.EXPERIMENT),
        "run_id": new_id(IdPrefix.RUN),
    }
