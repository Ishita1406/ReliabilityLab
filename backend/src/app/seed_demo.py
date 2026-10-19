"""Seed the configured database with clearly labelled DEMO data.

    APP_DATABASE_URL=... uv run python -m app.seed_demo

Registers the SYNTHETIC evaluation suite and agent versions, then creates and runs real
experiments through the normal runner, evaluator, and persistence (nothing is fabricated: every
stored run, trace, and evaluation was produced by executing the deterministic agent). Every
experiment name starts with ``[demo]`` and its description says it is synthetic demo data.
One experiment is left ``pending`` (never run) so the dashboard's empty states can be seen.

No human reviews are seeded: review labels must come from people.
"""

import argparse

from sqlalchemy.orm import Session, sessionmaker

from app.benchmarks.loader import load_bundled_suite
from app.db.session import create_db_engine, database_url, session_factory
from app.domain.agent import AgentConfig
from app.evaluation.engine import Evaluator
from app.experiments.samples import (
    BASELINE_VERSION,
    EVALUATION_SUITE,
    RESEARCH_AGENT,
    VARIANT_VERSIONS,
    research_agent_factory,
)
from app.experiments.service import ExperimentService
from app.runner.runner import ScenarioRunner
from app.runner.samples import SAMPLE_LIMITS

DEMO_DESCRIPTION = "DEMO DATA: synthetic benchmark, deterministic agent, seeded by app.seed_demo."
CONFIG = AgentConfig(
    id=BASELINE_VERSION.agent_config_id,
    name="deterministic-research",
    description="Deterministic regex-based research agent (no LLM).",
    created_at=BASELINE_VERSION.created_at,
)


def seed(sessions: sessionmaker[Session]) -> dict[str, str]:
    """Create the demo experiments; returns {label: experiment_id}."""
    service = ExperimentService(sessions)
    versions = {"baseline": BASELINE_VERSION, **VARIANT_VERSIONS}
    for version in versions.values():
        service.register(EVALUATION_SUITE, CONFIG, version)
    # The Milestone 2 dataset (5 cases) is registered for browsing only: the deterministic agent
    # cannot run it (Milestone 4 §10), so no experiment is created for it.
    service.register(load_bundled_suite("research-agent-sample", "1.0.0"), CONFIG, BASELINE_VERSION)
    evaluator = Evaluator()
    runner = ScenarioRunner(research_agent_factory)
    created: dict[str, str] = {}
    for label in ("baseline", "no-retry", "single-source", "pending"):
        version = versions["baseline" if label == "pending" else label]
        experiment = service.create_experiment(
            name=f"[demo] {label} ({version.version_label})",
            description=DEMO_DESCRIPTION,
            suite_id=EVALUATION_SUITE.id,
            suite_version=EVALUATION_SUITE.version,
            scenario_ids=[s.id for s in EVALUATION_SUITE.scenarios],
            agent_version_id=version.id,
            repetitions=1,
            seed=0,
            limits=SAMPLE_LIMITS,
            evaluator_version=evaluator.version,
            agent_implementation=RESEARCH_AGENT,
        )
        if label != "pending":
            service.run_experiment(experiment.id, runner, evaluator)
        created[label] = experiment.id
    return created


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None).parse_args(
        argv
    )
    engine = create_db_engine(database_url())
    try:
        created = seed(session_factory(engine))
    finally:
        engine.dispose()
    for label, experiment_id in created.items():
        print(f"{label}\t{experiment_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
