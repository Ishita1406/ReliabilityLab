"""Foreign keys, composite foreign keys, and CHECK constraints enforced by PostgreSQL."""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import delete, insert, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    BenchmarkScenarioRow,
    BenchmarkSuiteRow,
    ExperimentRow,
    ReviewAnnotationRow,
    RunRow,
    TraceEventRow,
)
from app.db.runs import load_trace
from app.domain.ids import IdPrefix, new_id
from app.runner.runner import ScenarioRunner
from app.runner.samples import SAMPLE_AGENT_VERSION, SAMPLE_LIMITS, deterministic_research_agent
from tests.db.helpers import SAMPLE_SUITE, make_service, scenario_ids


@pytest.fixture
def stored(sessions: sessionmaker[Session]) -> dict[str, str]:
    """Two experiments with one stored run each."""
    service = make_service(sessions)
    ids: dict[str, str] = {}
    for key in ("a", "b"):
        experiment = service.create_experiment(
            name=key,
            suite_id=SAMPLE_SUITE.id,
            suite_version=SAMPLE_SUITE.version,
            scenario_ids=scenario_ids("baseline"),
            agent_version_id=SAMPLE_AGENT_VERSION.id,
            repetitions=1,
            seed=0,
        )
        (run_id,) = service.run_experiment(
            experiment.id, ScenarioRunner(deterministic_research_agent), SAMPLE_LIMITS
        )
        ids[f"experiment_{key}"] = experiment.id
        ids[f"run_{key}"] = run_id
    return ids


def violates(session: Session, action: Callable[[], Any], constraint: str) -> None:
    """Run ``action`` in a savepoint and assert it violates the named constraint."""
    with pytest.raises(IntegrityError) as exc, session.begin_nested():
        action()
        session.flush()
    assert constraint in str(exc.value.orig)


def _event(run_id: str, experiment_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": new_id(IdPrefix.TRACE_EVENT),
        "run_id": run_id,
        "experiment_id": experiment_id,
        "parent_id": None,
        "sequence": 999,
        "kind": "agent_decision",
        "status": "ok",
        "started_at": text("now()"),
        "ended_at": text("now()"),
    }
    row.update(overrides)
    return row


def test_trace_event_requires_existing_run(session: Session, stored: dict[str, str]) -> None:
    root = load_trace(session, stored["run_a"]).root
    violates(
        session,
        lambda: session.execute(
            insert(TraceEventRow).values(
                _event(new_id(IdPrefix.RUN), stored["experiment_a"], parent_id=root.id)
            )
        ),
        "fk_trace_events_run_experiment",
    )


def test_trace_event_experiment_must_match_its_run(
    session: Session, stored: dict[str, str]
) -> None:
    root = load_trace(session, stored["run_a"]).root
    violates(
        session,
        lambda: session.execute(
            insert(TraceEventRow).values(
                _event(stored["run_a"], stored["experiment_b"], parent_id=root.id)
            )
        ),
        "fk_trace_events_run_experiment",
    )


def test_parent_event_must_be_in_the_same_run(session: Session, stored: dict[str, str]) -> None:
    other_root = load_trace(session, stored["run_b"]).root
    violates(
        session,
        lambda: session.execute(
            insert(TraceEventRow).values(
                _event(stored["run_a"], stored["experiment_a"], parent_id=other_root.id)
            )
        ),
        "fk_trace_events_parent_in_run",
    )


def test_only_the_root_may_lack_a_parent(session: Session, stored: dict[str, str]) -> None:
    violates(
        session,
        lambda: session.execute(
            insert(TraceEventRow).values(_event(stored["run_a"], stored["experiment_a"]))
        ),
        "ck_trace_events_root_iff_run",
    )


def test_sequence_numbers_are_unique_per_run(session: Session, stored: dict[str, str]) -> None:
    root = load_trace(session, stored["run_a"]).root
    violates(
        session,
        lambda: session.execute(
            insert(TraceEventRow).values(
                _event(stored["run_a"], stored["experiment_a"], parent_id=root.id, sequence=1)
            )
        ),
        "uq_trace_events_run_id_sequence",
    )


def test_run_scenario_must_belong_to_its_experiment(
    session: Session, stored: dict[str, str]
) -> None:
    other_scenario = session.scalar(
        text("SELECT pk FROM benchmark_scenarios WHERE slug = :slug"),
        {"slug": scenario_ids("fetch-misleading")[0]},
    )
    violates(
        session,
        lambda: session.execute(
            update(RunRow).where(RunRow.id == stored["run_a"]).values(scenario_pk=other_scenario)
        ),
        "fk_runs_scenario_in_experiment",
    )


def test_run_agent_version_must_match_its_experiment(
    session: Session, stored: dict[str, str]
) -> None:
    violates(
        session,
        lambda: session.execute(
            update(RunRow)
            .where(RunRow.id == stored["run_a"])
            .values(agent_version_id=new_id(IdPrefix.AGENT_VERSION))
        ),
        "fk_runs_agent_version_of_experiment",
    )


def test_completed_run_must_have_an_answer(session: Session, stored: dict[str, str]) -> None:
    violates(
        session,
        lambda: session.execute(
            update(RunRow).where(RunRow.id == stored["run_a"]).values(final_answer=None)
        ),
        "ck_runs_answer_iff_completed",
    )


def test_status_values_are_constrained(session: Session, stored: dict[str, str]) -> None:
    violates(
        session,
        # Clear the answer too, so the status CHECK is the only constraint violated.
        lambda: session.execute(
            text(
                "UPDATE runs SET status = 'succeeded', final_answer = NULL, "
                "declared_outcome = NULL WHERE id = :id"
            ),
            {"id": stored["run_a"]},
        ),
        "ck_runs_status_values",
    )


def test_one_baseline_per_case(session: Session, stored: dict[str, str]) -> None:
    violates(
        session,
        lambda: session.execute(
            update(BenchmarkScenarioRow)
            .where(BenchmarkScenarioRow.slug == scenario_ids("fetch-misleading")[0])
            .values(kind="baseline")
        ),
        "",  # either the baseline_no_override check or the partial unique index fires
    )


def test_suite_referenced_by_experiments_cannot_be_deleted(
    session: Session, stored: dict[str, str]
) -> None:
    violates(
        session,
        lambda: session.execute(delete(BenchmarkSuiteRow)),
        "fk_experiments_suite_pk_benchmark_suites",
    )


def test_deleting_an_experiment_cascades_to_runs_and_events(
    session: Session, stored: dict[str, str]
) -> None:
    session.execute(delete(ExperimentRow).where(ExperimentRow.id == stored["experiment_a"]))
    session.flush()

    assert session.get(RunRow, stored["run_a"]) is None
    assert (
        session.scalar(
            text("SELECT count(*) FROM trace_events WHERE run_id = :id"), {"id": stored["run_a"]}
        )
        == 0
    )
    assert session.get(RunRow, stored["run_b"]) is not None


def test_reviewed_runs_cannot_be_deleted(session: Session, stored: dict[str, str]) -> None:
    session.execute(
        insert(ReviewAnnotationRow).values(
            id=new_id(IdPrefix.REVIEW_ANNOTATION),
            target_type="run",
            run_id=stored["run_a"],
            reviewer="reviewer@example.com",
            verdict="agree",
            created_at=text("now()"),
        )
    )
    session.flush()

    violates(
        session,
        lambda: session.execute(
            delete(ExperimentRow).where(ExperimentRow.id == stored["experiment_a"])
        ),
        "fk_review_annotations_run_id_runs",
    )


def test_review_must_target_exactly_one_matching_entity(
    session: Session, stored: dict[str, str]
) -> None:
    event_id = load_trace(session, stored["run_a"]).root.id
    base = {
        "reviewer": "r",
        "verdict": "agree",
        "created_at": text("now()"),
    }
    violates(
        session,
        lambda: session.execute(
            insert(ReviewAnnotationRow).values(
                id=new_id(IdPrefix.REVIEW_ANNOTATION),
                target_type="run",
                run_id=stored["run_a"],
                trace_event_id=event_id,
                **base,
            )
        ),
        "ck_review_annotations_single_matching_target",
    )
    violates(
        session,
        lambda: session.execute(
            insert(ReviewAnnotationRow).values(
                id=new_id(IdPrefix.REVIEW_ANNOTATION),
                target_type="run",
                trace_event_id=event_id,
                **base,
            )
        ),
        "ck_review_annotations_single_matching_target",
    )
