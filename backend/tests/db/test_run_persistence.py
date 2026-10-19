"""Running experiments with persistence: outcomes, traces, failures, rollback, retention."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.agents.interface import (
    AgentDescriptor,
    AgentEnvironment,
    AgentOutput,
    AgentTask,
)
from app.benchmarks.loader import load_bundled_suite
from app.db.benchmarks import NotFoundError
from app.db.models import ExperimentRow, RunRow, TraceEventRow
from app.db.reviews import save_review
from app.db.runs import (
    DuplicateRunError,
    TraceMismatchError,
    list_run_ids,
    load_experiment,
    load_run,
    load_trace,
    purge_expired_traces,
    purge_traces,
    save_run,
)
from app.domain.enums import (
    AgentOutcome,
    ExperimentStatus,
    ReviewTargetType,
    ReviewVerdict,
    RunStatus,
)
from app.domain.review import HumanReviewAnnotation
from app.experiments.service import ExperimentService, ExperimentStateError, run_seed
from app.runner.runner import RunRequest, ScenarioRunner, ScenarioRunResult, TerminationReason
from app.runner.samples import SAMPLE_AGENT_VERSION, SAMPLE_LIMITS, deterministic_research_agent
from app.runtime.limits import ExecutionLimits
from app.tracing.collector import TraceCollector
from app.tracing.reconstruct import tool_calls
from app.tracing.schema import CollectedTrace, EventKind
from tests.db.helpers import SAMPLE_CONFIG, SAMPLE_SUITE, make_service, scenario_ids

RUNNER = ScenarioRunner(deterministic_research_agent)
ALL_SCENARIOS = [s.id for s in SAMPLE_SUITE.scenarios]


def create(
    service: ExperimentService, scenarios: list[str], repetitions: int = 1, seed: int = 0
) -> str:
    return service.create_experiment(
        name="persistence test",
        suite_id=SAMPLE_SUITE.id,
        suite_version=SAMPLE_SUITE.version,
        scenario_ids=scenarios,
        agent_version_id=SAMPLE_AGENT_VERSION.id,
        repetitions=repetitions,
        seed=seed,
    ).id


def expected_result(
    experiment_id: str,
    scenario_id: str,
    repetition: int,
    seed: int,
    limits: ExecutionLimits = SAMPLE_LIMITS,
) -> tuple[ScenarioRunResult, CollectedTrace]:
    """Re-run a single run in memory to compare with what the service stored."""
    scenario = SAMPLE_SUITE.get_scenario(scenario_id)
    result = RUNNER.run(
        RunRequest(
            experiment_id=experiment_id,
            agent_version=SAMPLE_AGENT_VERSION,
            case=SAMPLE_SUITE.get_case(scenario.case_id),
            scenario=scenario,
            limits=limits,
            seed=run_seed(seed, scenario_id, repetition),
            repetition=repetition,
        )
    )
    return result, TraceCollector().collect(result)


# --- full experiment ------------------------------------------------------------------------


def test_experiment_runs_are_stored_with_reconstructable_traces(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, ALL_SCENARIOS, repetitions=2, seed=5)

    run_ids = service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)

    assert len(run_ids) == len(ALL_SCENARIOS) * 2
    assert sorted(list_run_ids(session, experiment_id)) == sorted(run_ids)
    experiment = load_experiment(session, experiment_id)
    assert experiment.status is ExperimentStatus.COMPLETED
    for scenario_id in ALL_SCENARIOS:
        for repetition in range(2):
            result, collected = expected_result(experiment_id, scenario_id, repetition, 5)
            stored = load_run(session, result.run.id)
            trace = load_trace(session, result.run.id)

            assert stored.run == result.run
            assert trace == collected  # exact round trip of the whole event tree
            assert tool_calls(trace) == result.execution.tool_calls
            assert stored.trace_event_count == len(trace.events)


def test_trace_rows_preserve_order_parents_and_durations(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("fetch-rate-limited"))
    (run_id,) = service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)

    rows = session.scalars(
        select(TraceEventRow).where(TraceEventRow.run_id == run_id).order_by(TraceEventRow.sequence)
    ).all()

    assert [r.sequence for r in rows] == list(range(len(rows)))
    by_id = {r.id: r for r in rows}
    for row in rows[1:]:
        assert row.parent_id in by_id
        assert by_id[row.parent_id].sequence < row.sequence
    for row in rows:
        assert row.duration_ms == pytest.approx(
            (row.ended_at - row.started_at).total_seconds() * 1000
        )
    fault_rows = [r for r in rows if r.kind is EventKind.FAULT_INJECTED]
    assert len(fault_rows) == 1 and fault_rows[0].fault_pk is not None
    assert all(r.experiment_id == experiment_id for r in rows)


def test_running_an_experiment_twice_is_refused(sessions: sessionmaker[Session]) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline"))
    service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)

    with pytest.raises(ExperimentStateError, match="not pending"):
        service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)


def test_repeated_experiments_store_independent_identical_runs(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    first = create(service, scenario_ids("fetch-misleading"), seed=9)
    second = create(service, scenario_ids("fetch-misleading"), seed=9)

    (run_a,) = service.run_experiment(first, RUNNER, SAMPLE_LIMITS)
    (run_b,) = service.run_experiment(second, RUNNER, SAMPLE_LIMITS)

    assert run_a != run_b
    stored_a, stored_b = load_run(session, run_a), load_run(session, run_b)
    assert stored_a.final_answer == stored_b.final_answer
    assert stored_a.run.seed == stored_b.run.seed
    events_a = [(e.kind, e.status, e.tool_name) for e in load_trace(session, run_a).events]
    events_b = [(e.kind, e.status, e.tool_name) for e in load_trace(session, run_b).events]
    assert events_a == events_b


# --- failed runs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "limits", "status", "reason"),
    [
        (
            "baseline",
            ExecutionLimits(max_steps=40, max_tool_calls=2, deadline_seconds=120),
            RunStatus.LIMIT_EXCEEDED,
            TerminationReason.LIMIT_EXCEEDED,
        ),
        (
            "search-timeout-always",
            ExecutionLimits(max_steps=40, max_tool_calls=12, deadline_seconds=5),
            RunStatus.TIMED_OUT,
            TerminationReason.DEADLINE_EXCEEDED,
        ),
    ],
)
def test_failed_runs_are_persisted_with_trace_and_without_answer(
    sessions: sessionmaker[Session],
    session: Session,
    scenario: str,
    limits: ExecutionLimits,
    status: RunStatus,
    reason: TerminationReason,
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids(scenario))

    (run_id,) = service.run_experiment(experiment_id, RUNNER, limits)

    stored = load_run(session, run_id)
    assert stored.run.status is status
    assert stored.termination_reason is reason
    assert stored.final_answer is None and stored.declared_outcome is None
    assert stored.run.error is not None
    trace = load_trace(session, run_id)
    assert trace.events[-1].kind is EventKind.RUN_ERROR
    assert load_experiment(session, experiment_id).status is ExperimentStatus.COMPLETED


class _CrashingAgent:
    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(name="crashing", version="1")

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
        environment.note("about to fail with sk-test-0000000000000000FAKE in hand")
        environment.tools.call(
            "search_documents",
            {
                "query": "x",
                "api_key": "sk-test-0000000000000000FAKE",
                "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
            },
        )
        raise RuntimeError("upstream rejected token sk-test-0000000000000000FAKE")


def test_errored_runs_are_persisted_and_secrets_never_reach_the_database(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline"))

    (run_id,) = service.run_experiment(
        experiment_id, ScenarioRunner(lambda _v: _CrashingAgent()), SAMPLE_LIMITS
    )

    stored = load_run(session, run_id)
    assert stored.run.status is RunStatus.ERRORED
    assert stored.run.error is not None
    assert stored.run.error.error_type == "RuntimeError"
    assert "[REDACTED]" in stored.run.error.message
    leaks = session.execute(
        text(
            "SELECT 'runs' FROM runs WHERE runs::text LIKE :secret "
            "UNION ALL SELECT 'events' FROM trace_events WHERE trace_events::text LIKE :secret "
            "UNION ALL SELECT 'bearer' FROM trace_events WHERE trace_events::text LIKE :bearer"
        ),
        {"secret": "%sk-test-0000000000000000FAKE%", "bearer": "%abcdefghijklmnopqrstuvwxyz%"},
    ).all()
    assert leaks == []


def test_bundled_dataset_cases_without_agent_variables_persist_as_errored(
    sessions: sessionmaker[Session], session: Session
) -> None:
    suite = load_bundled_suite("research-agent-sample", "1.0.0")
    service = make_service(sessions)
    service.register(suite, SAMPLE_CONFIG, SAMPLE_AGENT_VERSION)
    experiment = service.create_experiment(
        name="bundled",
        suite_id=suite.id,
        suite_version=suite.version,
        scenario_ids=["research.fact-lookup.baseline"],
        agent_version_id=SAMPLE_AGENT_VERSION.id,
        repetitions=1,
        seed=0,
    )

    (run_id,) = service.run_experiment(experiment.id, RUNNER, SAMPLE_LIMITS)

    stored = load_run(session, run_id)
    assert stored.run.status is RunStatus.ERRORED
    assert stored.run.error is not None
    assert stored.run.error.error_type == "TaskSpecificationError"


# --- atomicity and rollback ------------------------------------------------------------------


def test_run_and_trace_are_written_atomically(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("fetch-rate-limited"))
    result, trace = expected_result(experiment_id, scenario_ids("fetch-rate-limited")[0], 0, 0)
    bad_events = [
        e.updated(fault_id="not-a-configured-fault") if e.kind is EventKind.FAULT_INJECTED else e
        for e in trace.events
    ]
    bad_trace = trace.updated(events=bad_events)

    with (
        pytest.raises(TraceMismatchError, match="not configured on scenario"),
        sessions.begin() as tx,
    ):
        save_run(tx, result, bad_trace)

    # The run row was flushed before the bad event was found; the rollback removed it.
    assert session.get(RunRow, result.run.id) is None
    assert session.scalar(select(func.count()).select_from(TraceEventRow)) == 0


def test_failure_mid_experiment_keeps_committed_runs_and_marks_experiment_failed(
    sessions: sessionmaker[Session], session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline", "fetch-misleading"))
    original = TraceCollector.collect
    calls = {"n": 0}

    def failing_collect(self: TraceCollector, result: ScenarioRunResult) -> CollectedTrace:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated collector failure")
        return original(self, result)

    monkeypatch.setattr(TraceCollector, "collect", failing_collect)

    with pytest.raises(RuntimeError, match="simulated collector failure"):
        service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)

    assert len(list_run_ids(session, experiment_id)) == 1
    row = session.get(ExperimentRow, experiment_id)
    assert row is not None and row.status is ExperimentStatus.FAILED
    assert row.finished_at is not None


def test_storing_the_same_run_twice_is_refused_without_side_effects(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline"))
    (run_id,) = service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)
    result, trace = expected_result(experiment_id, scenario_ids("baseline")[0], 0, 0)
    events_before = session.scalar(select(func.count()).select_from(TraceEventRow))

    with pytest.raises(DuplicateRunError), sessions.begin() as tx:
        save_run(tx, result, trace)

    assert session.scalar(select(func.count()).select_from(TraceEventRow)) == events_before
    assert load_trace(session, run_id) == trace


# --- retention ------------------------------------------------------------------------------


def test_purge_removes_old_traces_but_keeps_runs_and_reviewed_traces(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline", "fetch-misleading"))
    reviewed_run, purged_run = service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)
    reviewed_event = load_trace(session, reviewed_run).events[-1]
    with sessions.begin() as tx:
        save_review(
            tx,
            HumanReviewAnnotation(
                target_type=ReviewTargetType.TRACE_EVENT,
                target_id=reviewed_event.id,
                reviewer="reviewer@example.com",
                verdict=ReviewVerdict.AGREE,
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            ),
        )

    with sessions.begin() as tx:
        purged = purge_traces(
            tx,
            finished_before=datetime(2026, 1, 2, tzinfo=UTC),  # runs use the 2026-01-01 epoch
            now=datetime(2026, 3, 2, tzinfo=UTC),
        )

    assert purged == 1
    assert load_trace(session, reviewed_run).events[-1] == reviewed_event
    with pytest.raises(NotFoundError, match="purged"):
        load_trace(session, purged_run)
    stored = load_run(session, purged_run)
    assert stored.trace_purged_at == datetime(2026, 3, 2, tzinfo=UTC)
    assert stored.declared_outcome is AgentOutcome.INSUFFICIENT_INFORMATION
    with sessions.begin() as tx:
        assert (
            purge_traces(
                tx,
                finished_before=datetime(2026, 1, 2, tzinfo=UTC),
                now=datetime(2026, 3, 3, tzinfo=UTC),
            )
            == 0
        )


def test_retention_window_is_applied_relative_to_now(
    sessions: sessionmaker[Session], session: Session
) -> None:
    service = make_service(sessions)
    experiment_id = create(service, scenario_ids("baseline"))
    (run_id,) = service.run_experiment(experiment_id, RUNNER, SAMPLE_LIMITS)
    finished = load_run(session, run_id).run.finished_at
    assert finished is not None

    with sessions.begin() as tx:
        kept = purge_expired_traces(tx, retention_days=30, now=finished + timedelta(days=29))
    with sessions.begin() as tx:
        purged = purge_expired_traces(tx, retention_days=30, now=finished + timedelta(days=31))

    assert (kept, purged) == (0, 1)
    with pytest.raises(ValueError, match="at least 1"), sessions.begin() as tx:
        purge_expired_traces(tx, retention_days=0, now=finished)
