"""Versioned experiments in PostgreSQL: snapshots, stored evaluations, stored comparisons."""

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agents.interface import AgentDescriptor
from app.db.evaluations import load_experiment_results, load_run_evaluation
from app.db.models import ExperimentRow, RunEvaluationRow, RunRow
from app.db.runs import load_experiment, load_experiment_configuration, load_run
from app.domain.agent import AgentConfig
from app.domain.enums import ExperimentStatus
from app.evaluation.engine import Evaluator
from app.evaluation.judge import LLMJudge
from app.experiments.compare import EXIT_NOT_COMPARABLE, compare_stored
from app.experiments.comparison import UnitStatus, compare
from app.experiments.executor import ExperimentConfigurationError
from app.experiments.policy import RegressionPolicy
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
from tests.db.helpers import ticking_clock
from tests.evaluation.test_judge import FakeClient

CONFIG = AgentConfig(
    id=BASELINE_VERSION.agent_config_id,
    name="deterministic-research",
    created_at=BASELINE_VERSION.created_at,
)
EVALUATOR = Evaluator()
RUNNER = ScenarioRunner(research_agent_factory)
PREFIX = "runner.lighthouse-year."
SCENARIOS = [
    PREFIX + name
    for name in ("baseline", "search-timeout-once", "search-malformed-once", "fetch-latency")
]


@pytest.fixture
def service(sessions: sessionmaker[Session]) -> ExperimentService:
    service = ExperimentService(sessions, now=ticking_clock())
    for version in (BASELINE_VERSION, VARIANT_VERSIONS["no-retry"]):
        service.register(EVALUATION_SUITE, CONFIG, version)
    return service


def create(
    service: ExperimentService,
    version_id: str = BASELINE_VERSION.id,
    *,
    agent: AgentDescriptor = RESEARCH_AGENT,
    evaluator: Evaluator = EVALUATOR,
    repetitions: int = 1,
) -> str:
    return service.create_experiment(
        name="versioned",
        suite_id=EVALUATION_SUITE.id,
        suite_version=EVALUATION_SUITE.version,
        scenario_ids=SCENARIOS,
        agent_version_id=version_id,
        repetitions=repetitions,
        seed=3,
        limits=SAMPLE_LIMITS,
        evaluator_version=evaluator.version,
        agent_implementation=agent,
    ).id


def test_configuration_snapshot_is_stored_with_its_hash(
    service: ExperimentService, session: Session
) -> None:
    experiment_id = create(service, repetitions=2)

    configuration = load_experiment_configuration(session, experiment_id)
    assert configuration is not None
    assert configuration.agent_version == BASELINE_VERSION
    assert configuration.agent_implementation == RESEARCH_AGENT
    assert (configuration.repetitions, configuration.seed) == (2, 3)
    assert configuration.evaluator_version == EVALUATOR.version
    row = session.get(ExperimentRow, experiment_id)
    assert row is not None
    assert row.configuration_sha256 == configuration.fingerprint
    assert row.evaluator_version == EVALUATOR.version


def test_tampered_snapshot_is_detected(service: ExperimentService, session: Session) -> None:
    experiment_id = create(service)
    session.execute(
        update(ExperimentRow)
        .where(ExperimentRow.id == experiment_id)
        .values(
            configuration=text("jsonb_set(configuration, '{seed}', '99')"),
        )
    )
    with pytest.raises(ValueError, match="fails its hash check"):
        load_experiment_configuration(session, experiment_id)


def test_snapshot_columns_are_all_or_nothing(service: ExperimentService, session: Session) -> None:
    experiment_id = create(service)
    with pytest.raises(IntegrityError, match="configuration_complete"):
        session.execute(
            update(ExperimentRow)
            .where(ExperimentRow.id == experiment_id)
            .values(configuration_sha256=None)
        )
        session.flush()


def test_runs_are_stored_with_evaluations_and_snapshot_consistent_configuration(
    service: ExperimentService, session: Session
) -> None:
    experiment_id = create(service)
    run_ids = service.run_experiment(experiment_id, RUNNER, EVALUATOR)

    assert len(run_ids) == len(SCENARIOS)
    configuration = load_experiment_configuration(session, experiment_id)
    assert configuration is not None
    for run_id in run_ids:
        stored = load_run(session, run_id)
        effective = stored.effective_configuration
        assert effective["agent_version"] == configuration.agent_version.model_dump(mode="json")
        assert effective["agent"] == RESEARCH_AGENT.model_dump(mode="json")
        evaluation = load_run_evaluation(session, run_id, EVALUATOR.version)
        assert evaluation is not None and evaluation.run_id == run_id

    results = load_experiment_results(session, experiment_id)
    assert results.configuration == configuration
    assert len(results.runs) == len(SCENARIOS)
    assert all(r.usage == [] for r in results.runs)  # deterministic agent: known zero usage
    assert all(r.duration_seconds is not None for r in results.runs)


def test_stored_experiments_compare_like_in_memory_ones(
    service: ExperimentService, session: Session
) -> None:
    baseline = create(service)
    candidate = create(service, VARIANT_VERSIONS["no-retry"].id)
    service.run_experiment(baseline, RUNNER, EVALUATOR)
    service.run_experiment(candidate, RUNNER, EVALUATOR)

    comparison = compare(
        load_experiment_results(session, baseline), load_experiment_results(session, candidate)
    )
    newly_failing = {
        u.scenario_id.removeprefix(PREFIX) for u in comparison.by_status(UnitStatus.NEWLY_FAILING)
    }
    assert newly_failing == {"search-timeout-once", "search-malformed-once"}

    code, report = compare_stored(
        sessions_of(session), baseline, candidate, policy=RegressionPolicy()
    )
    assert code == 1
    assert "newly_failing: 2" in report


def sessions_of(session: Session) -> sessionmaker[Session]:
    return sessionmaker(bind=session.connection(), join_transaction_mode="create_savepoint")


def test_mismatched_agent_implementation_fails_the_experiment(
    service: ExperimentService, session: Session
) -> None:
    experiment_id = create(service, agent=AgentDescriptor(name="someone-else", version="1"))
    with pytest.raises(ExperimentConfigurationError, match="agent implementation"):
        service.run_experiment(experiment_id, RUNNER, EVALUATOR)
    assert load_experiment(session, experiment_id).status is ExperimentStatus.FAILED
    assert session.scalar(select(func.count()).select_from(RunRow)) == 0


def test_mismatched_evaluator_is_refused_before_running(
    service: ExperimentService, session: Session
) -> None:
    experiment_id = create(service)
    with pytest.raises(ExperimentConfigurationError, match="evaluator"):
        service.run_experiment(experiment_id, RUNNER, Evaluator(LLMJudge(FakeClient([]))))
    assert load_experiment(session, experiment_id).status is ExperimentStatus.PENDING


def test_legacy_experiment_without_snapshot_cannot_be_compared(
    service: ExperimentService, session: Session
) -> None:
    baseline = create(service)
    candidate = create(service)
    service.run_experiment(baseline, RUNNER, EVALUATOR)
    service.run_experiment(candidate, RUNNER, EVALUATOR)
    session.execute(
        update(ExperimentRow)
        .where(ExperimentRow.id == baseline)
        .values(configuration=None, configuration_sha256=None, evaluator_version=None)
    )
    assert load_experiment_results(session, baseline).configuration is None
    code, report = compare_stored(
        sessions_of(session), baseline, candidate, policy=RegressionPolicy()
    )
    assert code == EXIT_NOT_COMPARABLE
    assert "no configuration snapshot" in report


def test_deleted_runs_show_up_as_missing(service: ExperimentService, session: Session) -> None:
    baseline = create(service)
    candidate = create(service)
    service.run_experiment(baseline, RUNNER, EVALUATOR)
    run_ids = service.run_experiment(candidate, RUNNER, EVALUATOR)
    session.execute(delete(RunRow).where(RunRow.id == run_ids[-1]))  # cascades trace + evaluation
    assert session.get(RunEvaluationRow, (run_ids[-1], EVALUATOR.version)) is None

    comparison = compare(
        load_experiment_results(session, baseline), load_experiment_results(session, candidate)
    )
    [missing] = comparison.by_status(UnitStatus.NOT_COMPARABLE)
    assert missing.scenario_id == SCENARIOS[-1]
    assert missing.reason == "candidate: 1 of 1 planned runs missing"
    assert comparison.candidate_metrics.missing_runs == 1


def test_unknown_experiment_is_not_comparable(service: ExperimentService, session: Session) -> None:
    baseline = create(service)
    code, report = compare_stored(
        sessions_of(session), baseline, "exp_" + "f" * 32, policy=RegressionPolicy()
    )
    assert code == EXIT_NOT_COMPARABLE and "not found" in report
