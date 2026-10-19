"""SYNTHETIC experiment fixtures: the Milestone 6 evaluation samples as a versioned suite, and
agent versions that differ in prompt or configuration.

Agent configurations are selected by ``AgentVersion.model.name`` (recorded in every
experiment's snapshot), so two experiments can run the same benchmark against different
configurations through the unchanged ``Agent`` interface.
"""

from app.agents.interface import Agent, AgentDescriptor
from app.agents.research import DeterministicResearchAgent, ResearchAgentConfig
from app.domain.agent import AgentVersion
from app.domain.benchmark import BenchmarkSuite
from app.domain.ids import IdPrefix, derived_id
from app.evaluation.engine import Evaluator
from app.evaluation.samples import EVALUATION_CASE, EVALUATION_SCENARIOS
from app.experiments.configuration import ExperimentConfiguration
from app.experiments.executor import run_in_memory
from app.experiments.records import ExperimentResults
from app.runner.runner import DEFAULT_EPOCH, ScenarioRunner
from app.runner.samples import SAMPLE_AGENT_VERSION, SAMPLE_LIMITS
from app.runtime.limits import ExecutionLimits

EVALUATION_SUITE = BenchmarkSuite(
    schema_version="1.0",
    id="evaluation-samples",
    version="1.0.0",
    name="Evaluation samples (synthetic)",
    description="SYNTHETIC: the lighthouse case under every runner fault, with the Milestone 6 "
    "evaluation expectations.",
    synthetic=True,
    created_at=DEFAULT_EPOCH,
    cases=[EVALUATION_CASE],
    scenarios=list(EVALUATION_SCENARIOS.values()),
)

RESEARCH_AGENT: AgentDescriptor = DeterministicResearchAgent().descriptor

AGENT_CONFIGS: dict[str, ResearchAgentConfig] = {
    "regex-extractor": ResearchAgentConfig(),
    "regex-extractor-no-retry": ResearchAgentConfig(max_retries=0),
    "regex-extractor-single-source": ResearchAgentConfig(max_sources=1),
    "regex-extractor-slow-backoff": ResearchAgentConfig(retry_backoff_ms=5_000),
}
"""Deterministic agent configurations by model name."""


def agent_version(
    label: str, *, model_name: str = "regex-extractor", system_prompt: str | None = None
) -> AgentVersion:
    return SAMPLE_AGENT_VERSION.updated(
        id=derived_id(IdPrefix.AGENT_VERSION, "deterministic-research", label),
        version_label=label,
        model={"provider": "deterministic", "name": model_name},
        system_prompt=system_prompt or SAMPLE_AGENT_VERSION.system_prompt,
    )


BASELINE_VERSION = agent_version("0.1.0")
VARIANT_VERSIONS: dict[str, AgentVersion] = {
    "prompt-b": agent_version(
        "0.1.0-prompt-b",
        system_prompt="(none: deterministic agent without a language model; prompt revision B)",
    ),
    "no-retry": agent_version("0.2.0-no-retry", model_name="regex-extractor-no-retry"),
    "single-source": agent_version(
        "0.2.0-single-source", model_name="regex-extractor-single-source"
    ),
    "slow-backoff": agent_version("0.2.0-slow-backoff", model_name="regex-extractor-slow-backoff"),
}


def research_agent_factory(version: AgentVersion) -> Agent:
    try:
        config = AGENT_CONFIGS[version.model.name]
    except KeyError:
        raise ValueError(f"no deterministic agent configuration '{version.model.name}'") from None
    return DeterministicResearchAgent(config)


def sample_configuration(
    version: AgentVersion,
    *,
    scenario_ids: list[str] | None = None,
    repetitions: int = 1,
    seed: int = 0,
    limits: ExecutionLimits = SAMPLE_LIMITS,
    evaluator: Evaluator | None = None,
    suite: BenchmarkSuite = EVALUATION_SUITE,
    agent: AgentDescriptor = RESEARCH_AGENT,
) -> ExperimentConfiguration:
    return ExperimentConfiguration.build(
        suite=suite,
        scenario_ids=scenario_ids or [s.id for s in suite.scenarios],
        agent_version=version,
        agent_implementation=agent,
        limits=limits,
        repetitions=repetitions,
        seed=seed,
        evaluator_version=(evaluator or Evaluator()).version,
    )


def run_sample_experiment(
    name: str,
    configuration: ExperimentConfiguration,
    *,
    runner: ScenarioRunner | None = None,
    suite: BenchmarkSuite = EVALUATION_SUITE,
    evaluator: Evaluator | None = None,
) -> ExperimentResults:
    return run_in_memory(
        derived_id(IdPrefix.EXPERIMENT, "sample", name),
        configuration,
        suite,
        runner or ScenarioRunner(research_agent_factory),
        evaluator or Evaluator(),
    )
