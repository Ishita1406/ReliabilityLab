"""Versioned experiment configuration snapshots.

An ``ExperimentConfiguration`` is recorded when an experiment is created and never changes.
It pins everything that determines the experiment's results:

* the benchmark suite (id, version, and a content hash of the whole suite) plus a fingerprint
  of every selected scenario (its case and scenario definitions, including the effective
  expectation), so comparisons can tell whether a scenario's definition changed;
* the full ``AgentVersion`` (model, prompt, tools, parameters) and its content hash;
* the agent implementation (``AgentDescriptor``: code name and version);
* execution limits, repetitions, and the base seed;
* the evaluator version and the runner version.

Every run's ``EffectiveRunConfiguration`` (stored per run since Milestone 5) is checked against
this snapshot when the run is recorded, so runs cannot silently diverge from it.
"""

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from app.agents.interface import AgentDescriptor
from app.domain.agent import AgentVersion
from app.domain.base import DomainModel, NonEmptyStr, SemVer, require_unique
from app.domain.benchmark import BenchmarkSuite
from app.domain.hashing import canonical_json, content_sha256
from app.domain.ids import Slug
from app.runner.runner import RUNNER_VERSION, EffectiveRunConfiguration
from app.runtime.limits import ExecutionLimits

Sha256 = str


def scenario_fingerprint(suite: BenchmarkSuite, scenario_id: str) -> Sha256:
    """Hash of everything that defines a scenario's task and scoring: its case (inputs and
    expectation) and the scenario itself (faults and any expectation override)."""
    scenario = suite.get_scenario(scenario_id)
    case = suite.get_case(scenario.case_id)
    payload = json.dumps(
        {"case": case.model_dump(mode="json"), "scenario": scenario.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConfigurationDifference(DomainModel):
    field: NonEmptyStr
    baseline: str
    candidate: str


class ExperimentConfiguration(DomainModel):
    format_version: Literal["1"] = "1"
    suite_id: Slug
    suite_version: SemVer
    suite_sha256: Sha256
    scenario_ids: list[Slug] = Field(min_length=1)
    scenario_case_ids: dict[Slug, Slug]
    scenario_fingerprints: dict[Slug, Sha256]
    agent_version: AgentVersion
    agent_version_sha256: Sha256
    agent_implementation: AgentDescriptor
    limits: ExecutionLimits
    repetitions: PositiveInt
    seed: NonNegativeInt
    evaluator_version: NonEmptyStr
    runner_version: NonEmptyStr

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique(self.scenario_ids, "scenario_ids")
        if set(self.scenario_fingerprints) != set(self.scenario_ids) or set(
            self.scenario_case_ids
        ) != set(self.scenario_ids):
            raise ValueError("fingerprints and case IDs must cover exactly the scenario_ids")
        if content_sha256(self.agent_version) != self.agent_version_sha256:
            raise ValueError("agent_version_sha256 does not match the agent version snapshot")
        return self

    @classmethod
    def build(
        cls,
        *,
        suite: BenchmarkSuite,
        scenario_ids: list[str],
        agent_version: AgentVersion,
        agent_implementation: AgentDescriptor,
        limits: ExecutionLimits,
        repetitions: int,
        seed: int,
        evaluator_version: str,
    ) -> Self:
        for scenario_id in scenario_ids:
            suite.get_scenario(scenario_id)  # KeyError for unknown scenarios
        return cls(
            suite_id=suite.id,
            suite_version=suite.version,
            suite_sha256=content_sha256(suite),
            scenario_ids=list(scenario_ids),
            scenario_case_ids={s: suite.get_scenario(s).case_id for s in scenario_ids},
            scenario_fingerprints={s: scenario_fingerprint(suite, s) for s in scenario_ids},
            agent_version=agent_version,
            agent_version_sha256=content_sha256(agent_version),
            agent_implementation=agent_implementation,
            limits=limits,
            repetitions=repetitions,
            seed=seed,
            evaluator_version=evaluator_version,
            runner_version=RUNNER_VERSION,
        )

    @property
    def fingerprint(self) -> Sha256:
        return hashlib.sha256(canonical_json(self).encode("utf-8")).hexdigest()

    def run_mismatches(self, run: EffectiveRunConfiguration) -> list[str]:
        """Ways in which a run's recorded configuration contradicts this snapshot."""
        problems = []
        if run.agent_version != self.agent_version:
            problems.append("agent version")
        if run.agent != self.agent_implementation:
            problems.append(
                f"agent implementation {run.agent.name}@{run.agent.version} != "
                f"{self.agent_implementation.name}@{self.agent_implementation.version}"
            )
        if run.limits != self.limits:
            problems.append("execution limits")
        if run.runner_version != self.runner_version:
            problems.append("runner version")
        if run.scenario_id not in self.scenario_fingerprints:
            problems.append(f"scenario '{run.scenario_id}' is not part of the experiment")
        return problems

    def differences(self, other: "ExperimentConfiguration") -> list[ConfigurationDifference]:
        """Human-readable differences that matter when comparing two experiments."""

        def text(value: object) -> str:
            if isinstance(value, DomainModel):
                return json.dumps(value.model_dump(mode="json"), sort_keys=True)
            return str(value)

        mine, theirs = self.agent_version, other.agent_version
        pairs: list[tuple[str, object, object]] = [
            (
                "suite",
                f"{self.suite_id}@{self.suite_version}",
                f"{other.suite_id}@{other.suite_version}",
            ),
            ("suite_sha256", self.suite_sha256, other.suite_sha256),
            ("scenario_ids", self.scenario_ids, other.scenario_ids),
            ("agent_version", mine.version_label, theirs.version_label),
            (
                "agent_model",
                f"{mine.model.provider}/{mine.model.name}",
                f"{theirs.model.provider}/{theirs.model.name}",
            ),
            ("system_prompt", mine.system_prompt, theirs.system_prompt),
            ("agent_tools", mine.tools, theirs.tools),
            ("agent_parameters", mine.parameters, theirs.parameters),
            (
                "agent_implementation",
                f"{self.agent_implementation.name}@{self.agent_implementation.version}",
                f"{other.agent_implementation.name}@{other.agent_implementation.version}",
            ),
            ("limits", self.limits, other.limits),
            ("repetitions", self.repetitions, other.repetitions),
            ("seed", self.seed, other.seed),
            ("evaluator_version", self.evaluator_version, other.evaluator_version),
            ("runner_version", self.runner_version, other.runner_version),
        ]
        return [
            ConfigurationDifference(field=name, baseline=text(a), candidate=text(b))
            for name, a, b in pairs
            if text(a) != text(b)
        ]
