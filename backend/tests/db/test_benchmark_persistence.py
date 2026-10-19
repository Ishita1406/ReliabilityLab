"""Suites, agents, and experiments in PostgreSQL."""

import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.benchmarks.loader import load_bundled_suite
from app.db.benchmarks import (
    ContentConflictError,
    NotFoundError,
    canonical_json,
    load_agent_version,
    load_suite,
    save_agent,
    save_suite,
)
from app.db.models import (
    AgentConfigRow,
    AgentVersionRow,
    BenchmarkSuiteRow,
    ExperimentRow,
    ScenarioFaultRow,
)
from app.db.runs import load_experiment
from app.domain.benchmark import BenchmarkSuite
from app.runner.samples import SAMPLE_AGENT_VERSION
from tests.db.helpers import SAMPLE_CONFIG, SAMPLE_SUITE, make_service, scenario_ids


@pytest.mark.parametrize(
    "suite",
    [SAMPLE_SUITE, load_bundled_suite("research-agent-sample", "1.0.0")],
    ids=["runner-samples", "research-agent-sample"],
)
def test_suite_round_trips_losslessly(session: Session, suite: BenchmarkSuite) -> None:
    save_suite(session, suite)
    session.expunge_all()

    loaded = load_suite(session, suite.id, suite.version)

    assert loaded == suite
    # JSONB normalises object key order, so compare canonical (key-sorted) JSON, not bytes.
    assert canonical_json(loaded) == canonical_json(suite)


def test_resaving_a_suite_loaded_from_the_database_is_idempotent(session: Session) -> None:
    first = save_suite(session, SAMPLE_SUITE)
    session.expunge_all()
    loaded = load_suite(session, SAMPLE_SUITE.id, SAMPLE_SUITE.version)

    assert save_suite(session, loaded).pk == first.pk  # no false content conflict


def test_every_fault_is_stored_relationally(session: Session) -> None:
    save_suite(session, SAMPLE_SUITE)

    stored = session.scalar(select(func.count()).select_from(ScenarioFaultRow))

    assert stored == sum(len(s.faults) for s in SAMPLE_SUITE.scenarios)


def test_saving_the_same_suite_twice_is_idempotent(session: Session) -> None:
    first = save_suite(session, SAMPLE_SUITE)
    second = save_suite(session, SAMPLE_SUITE)

    assert first.pk == second.pk
    assert session.scalar(select(func.count()).select_from(BenchmarkSuiteRow)) == 1


def test_changed_content_under_an_existing_version_is_refused(session: Session) -> None:
    save_suite(session, SAMPLE_SUITE)
    changed = SAMPLE_SUITE.updated(name="Renamed without a version bump")

    with pytest.raises(ContentConflictError, match="publish a new version"):
        save_suite(session, changed)


def test_new_version_of_a_suite_is_stored_separately(session: Session) -> None:
    save_suite(session, SAMPLE_SUITE)
    save_suite(session, SAMPLE_SUITE.updated(version="1.1.0", name="v1.1"))

    assert load_suite(session, SAMPLE_SUITE.id, "1.1.0").name == "v1.1"
    assert load_suite(session, SAMPLE_SUITE.id, "1.0.0").name == SAMPLE_SUITE.name


def test_unknown_suite_raises_not_found(session: Session) -> None:
    with pytest.raises(NotFoundError):
        load_suite(session, "nope", "1.0.0")


def test_agent_round_trip_idempotency_and_conflicts(session: Session) -> None:
    save_agent(session, SAMPLE_CONFIG, SAMPLE_AGENT_VERSION)
    save_agent(session, SAMPLE_CONFIG, SAMPLE_AGENT_VERSION)

    assert load_agent_version(session, SAMPLE_AGENT_VERSION.id) == SAMPLE_AGENT_VERSION
    assert session.scalar(select(func.count()).select_from(AgentVersionRow)) == 1
    assert session.scalar(select(func.count()).select_from(AgentConfigRow)) == 1
    with pytest.raises(ContentConflictError):
        save_agent(
            session,
            SAMPLE_CONFIG,
            SAMPLE_AGENT_VERSION.updated(system_prompt="changed in place"),
        )
    with pytest.raises(ValueError, match="does not belong"):
        save_agent(session, SAMPLE_CONFIG.updated(id="agt_" + "0" * 32), SAMPLE_AGENT_VERSION)


def test_repeated_experiment_creation_yields_distinct_experiments(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    service.register(SAMPLE_SUITE, SAMPLE_CONFIG, SAMPLE_AGENT_VERSION)  # again: idempotent

    experiments = [
        service.create_experiment(
            name="same settings",
            suite_id=SAMPLE_SUITE.id,
            suite_version=SAMPLE_SUITE.version,
            scenario_ids=scenario_ids("baseline", "fetch-misleading"),
            agent_version_id=SAMPLE_AGENT_VERSION.id,
            repetitions=2,
            seed=1,
        )
        for _ in range(3)
    ]

    assert len({e.id for e in experiments}) == 3
    assert session.scalar(select(func.count()).select_from(ExperimentRow)) == 3
    assert session.scalar(select(func.count()).select_from(BenchmarkSuiteRow)) == 1
    for experiment in experiments:
        assert load_experiment(session, experiment.id) == experiment


def test_experiment_with_unknown_scenario_is_refused(sessions: sessionmaker[Session]) -> None:
    service = make_service(sessions)

    with pytest.raises(NotFoundError, match=re.escape("scenario 'no.such' not found")):
        service.create_experiment(
            name="bad",
            suite_id=SAMPLE_SUITE.id,
            suite_version=SAMPLE_SUITE.version,
            scenario_ids=["no.such"],
            agent_version_id=SAMPLE_AGENT_VERSION.id,
            repetitions=1,
            seed=0,
        )
