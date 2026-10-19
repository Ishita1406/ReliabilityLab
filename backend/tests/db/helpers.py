"""Shared synthetic content for database tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy.orm import Session, sessionmaker

from app.agents.interface import AgentDescriptor
from app.domain.agent import AgentConfig
from app.domain.benchmark import BenchmarkSuite
from app.evaluation.engine import Evaluator
from app.experiments.service import ExperimentService
from app.runner.runner import DEFAULT_EPOCH
from app.runner.samples import SAMPLE_AGENT_VERSION, SAMPLE_CASE, SAMPLE_LIMITS, SCENARIOS
from app.runtime.limits import ExecutionLimits

SAMPLE_SUITE = BenchmarkSuite(
    schema_version="1.0",
    id="runner-samples",
    version="1.0.0",
    name="Runner samples (synthetic)",
    description="SYNTHETIC: the Milestone 4 sample case with one scenario per fault type.",
    synthetic=True,
    created_at=DEFAULT_EPOCH,
    cases=[SAMPLE_CASE],
    scenarios=list(SCENARIOS.values()),
)

SAMPLE_CONFIG = AgentConfig(
    id=SAMPLE_AGENT_VERSION.agent_config_id,
    name="deterministic-research",
    description="Deterministic regex-based research agent.",
    created_at=DEFAULT_EPOCH,
)


EVALUATOR = Evaluator()
DETERMINISTIC_AGENT = AgentDescriptor(name="deterministic-research", version="0.1.0")


class Versioning(TypedDict):
    limits: ExecutionLimits
    evaluator_version: str
    agent_implementation: AgentDescriptor


def versioning(
    limits: ExecutionLimits = SAMPLE_LIMITS, agent: AgentDescriptor = DETERMINISTIC_AGENT
) -> Versioning:
    """The Milestone 7 versioning arguments of ``ExperimentService.create_experiment``."""
    return Versioning(
        limits=limits, evaluator_version=EVALUATOR.version, agent_implementation=agent
    )


def ticking_clock(start: datetime = datetime(2026, 2, 1, tzinfo=UTC)) -> Callable[[], datetime]:
    """Deterministic 'now': advances one second per call."""
    state = {"now": start}

    def now() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return now


def make_service(sessions: sessionmaker[Session]) -> ExperimentService:
    service = ExperimentService(sessions, now=ticking_clock())
    service.register(SAMPLE_SUITE, SAMPLE_CONFIG, SAMPLE_AGENT_VERSION)
    return service


def scenario_ids(*names: str) -> list[str]:
    return [SCENARIOS[name].id for name in names]
