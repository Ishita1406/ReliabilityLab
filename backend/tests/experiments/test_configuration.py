"""Configuration snapshots: content, stability, and enforcement against runs."""

import pytest

from app.agents.interface import AgentDescriptor
from app.agents.research import DeterministicResearchAgent
from app.domain.agent import AgentVersion
from app.evaluation.engine import Evaluator
from app.evaluation.judge import LLMJudge
from app.experiments.configuration import ExperimentConfiguration, scenario_fingerprint
from app.experiments.executor import ExperimentConfigurationError, run_in_memory, run_seed
from app.experiments.samples import (
    BASELINE_VERSION,
    EVALUATION_SUITE,
    RESEARCH_AGENT,
    VARIANT_VERSIONS,
    run_sample_experiment,
    sample_configuration,
)
from app.runner.runner import RUNNER_VERSION, ScenarioRunner
from app.runner.samples import SAMPLE_LIMITS
from app.runtime.limits import ExecutionLimits
from tests.evaluation.test_judge import FakeClient

SCENARIOS = [s.id for s in EVALUATION_SUITE.scenarios]


def test_snapshot_pins_suite_agent_scenarios_limits_and_evaluator() -> None:
    config = sample_configuration(BASELINE_VERSION, repetitions=3, seed=7)

    assert (config.suite_id, config.suite_version) == ("evaluation-samples", "1.0.0")
    assert config.scenario_ids == SCENARIOS
    assert set(config.scenario_fingerprints) == set(SCENARIOS)
    assert config.agent_version == BASELINE_VERSION
    assert config.agent_implementation == AgentDescriptor(
        name="deterministic-research", version="0.1.0"
    )
    assert (config.repetitions, config.seed, config.limits) == (3, 7, SAMPLE_LIMITS)
    assert config.evaluator_version == "evaluator-0.1.0"
    assert config.runner_version == RUNNER_VERSION


def test_fingerprint_is_stable_and_survives_a_json_round_trip() -> None:
    first = sample_configuration(BASELINE_VERSION)
    second = sample_configuration(BASELINE_VERSION)
    assert first.fingerprint == second.fingerprint
    reloaded = ExperimentConfiguration.model_validate_json(first.model_dump_json())
    assert reloaded == first and reloaded.fingerprint == first.fingerprint


@pytest.mark.parametrize(
    "change",
    [
        {"seed": 1},
        {"repetitions": 2},
        {"limits": ExecutionLimits(max_steps=40, max_tool_calls=12, deadline_seconds=60)},
        {"agent_version": VARIANT_VERSIONS["prompt-b"]},
        {"evaluator": Evaluator(LLMJudge(FakeClient([])))},
        {"agent": AgentDescriptor(name="deterministic-research", version="0.2.0")},
    ],
)
def test_any_relevant_change_changes_the_fingerprint(change: dict[str, object]) -> None:
    version = change.pop("agent_version", BASELINE_VERSION)
    assert isinstance(version, AgentVersion)
    changed = sample_configuration(version, **change)  # type: ignore[arg-type]
    assert changed.fingerprint != sample_configuration(BASELINE_VERSION).fingerprint


def test_tampered_agent_snapshot_is_rejected() -> None:
    data = sample_configuration(BASELINE_VERSION).model_dump(mode="json")
    data["agent_version"]["system_prompt"] = "edited after the fact"
    with pytest.raises(ValueError, match="agent_version_sha256 does not match"):
        ExperimentConfiguration.model_validate(data)


def test_scenario_fingerprint_tracks_expectation_changes() -> None:
    scenario_id = SCENARIOS[0]
    before = scenario_fingerprint(EVALUATION_SUITE, scenario_id)
    case = EVALUATION_SUITE.cases[0]
    expectation = case.expectation.updated(scoring={"pass_threshold": 0.5})
    changed = EVALUATION_SUITE.updated(cases=[case.updated(expectation=expectation.model_dump())])
    assert scenario_fingerprint(changed, scenario_id) != before


def test_differences_name_the_changed_fields() -> None:
    base = sample_configuration(BASELINE_VERSION)
    prompt = sample_configuration(VARIANT_VERSIONS["prompt-b"])
    assert [d.field for d in base.differences(prompt)] == ["agent_version", "system_prompt"]
    assert base.differences(base) == []


def test_every_run_records_the_snapshotted_configuration() -> None:
    config = sample_configuration(BASELINE_VERSION, scenario_ids=SCENARIOS[:2], repetitions=2)
    results = run_sample_experiment("snapshot", config)

    assert len(results.runs) == 4
    for record in results.runs:
        assert record.evaluation is not None
        assert record.evaluation.evaluator_version == config.evaluator_version
    # Seeds are derived from the snapshot's base seed, so reruns are reproducible.
    rerun = run_sample_experiment("snapshot", config)
    assert [r.run_id for r in rerun.runs] == [r.run_id for r in results.runs]
    assert run_seed(0, SCENARIOS[0], 1) != run_seed(0, SCENARIOS[0], 0)


def test_runner_with_a_different_implementation_is_refused() -> None:
    config = sample_configuration(
        BASELINE_VERSION, agent=AgentDescriptor(name="other-agent", version="9.9.9")
    )
    with pytest.raises(ExperimentConfigurationError, match="agent implementation"):
        run_sample_experiment("mismatch", config)


def test_evaluator_and_suite_must_match_the_snapshot() -> None:
    config = sample_configuration(BASELINE_VERSION)
    runner = ScenarioRunner(lambda _version: DeterministicResearchAgent())
    with pytest.raises(ExperimentConfigurationError, match="evaluator"):
        run_in_memory(
            "exp_" + "0" * 32, config, EVALUATION_SUITE, runner, Evaluator(LLMJudge(FakeClient([])))
        )
    newer = EVALUATION_SUITE.updated(version="1.1.0")
    with pytest.raises(ExperimentConfigurationError, match="is not the experiment's"):
        run_in_memory("exp_" + "0" * 32, config, newer, runner, Evaluator())
    edited = EVALUATION_SUITE.updated(
        scenarios=[
            EVALUATION_SUITE.scenarios[0].updated(description="edited in place"),
            *EVALUATION_SUITE.scenarios[1:],
        ]
    )
    with pytest.raises(ExperimentConfigurationError, match="changed since creation"):
        run_in_memory("exp_" + "0" * 32, config, edited, runner, Evaluator())


def test_research_agent_descriptor_is_the_recorded_implementation() -> None:
    assert DeterministicResearchAgent().descriptor == RESEARCH_AGENT
