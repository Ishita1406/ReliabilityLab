"""Persistence for experiments, runs, and run traces, plus trace retention.

``save_run`` writes a run's outcome and its complete trace in the caller's transaction, so a
run is stored with its trace or not at all. Free text and JSON that originate from agent or tool
behaviour are sanitised with the trace payload policy before they are stored, including the
copies kept on the ``runs`` row.
"""

from datetime import datetime, timedelta

from pydantic import JsonValue
from sqlalchemy import delete, exists, insert, select, update
from sqlalchemy.orm import Session

from app.db.benchmarks import NotFoundError, fault_pks, find_suite_row, scenario_row
from app.db.models import (
    BenchmarkCaseRow,
    BenchmarkScenarioRow,
    BenchmarkSuiteRow,
    ExperimentRow,
    ExperimentScenarioRow,
    FailureEvidenceRow,
    ReviewAnnotationRow,
    RunRow,
    ScenarioFaultRow,
    TraceEventRow,
)
from app.domain.base import DomainModel
from app.domain.enums import AgentOutcome
from app.domain.experiment import Experiment, Run, RunError
from app.domain.ids import RunId
from app.runner.runner import ScenarioRunResult, TerminationReason
from app.runtime.limits import LimitKind
from app.tracing.policy import DEFAULT_POLICY, TracePayloadPolicy, sanitize
from app.tracing.schema import CollectedTrace, TraceEventRecord


class DuplicateRunError(Exception):
    """A run with this ID is already stored; runs are immutable once recorded."""


class TraceMismatchError(ValueError):
    """The trace does not belong to the run, or references a fault outside its scenario."""


# --- experiments ------------------------------------------------------------------------------


def create_experiment(session: Session, experiment: Experiment) -> ExperimentRow:
    suite_row = find_suite_row(session, experiment.suite_id, experiment.suite_version)
    if suite_row is None:
        raise NotFoundError(
            f"suite '{experiment.suite_id}' version '{experiment.suite_version}' not registered"
        )
    row = ExperimentRow(
        id=experiment.id,
        name=experiment.name,
        description=experiment.description,
        suite_pk=suite_row.pk,
        agent_version_id=experiment.agent_version_id,
        repetitions=experiment.repetitions,
        seed=experiment.seed,
        status=experiment.status,
        created_at=experiment.created_at,
        started_at=experiment.started_at,
        finished_at=experiment.finished_at,
    )
    session.add(row)
    session.flush()
    for position, scenario_slug in enumerate(experiment.scenario_ids):
        scenario = scenario_row(session, suite_row.pk, scenario_slug)
        session.add(
            ExperimentScenarioRow(
                experiment_id=row.id,
                scenario_pk=scenario.pk,
                suite_pk=suite_row.pk,
                position=position,
            )
        )
    session.flush()
    return row


def load_experiment(session: Session, experiment_id: str) -> Experiment:
    row = session.get(ExperimentRow, experiment_id)
    if row is None:
        raise NotFoundError(f"experiment '{experiment_id}' not found")
    suite_row = session.get(BenchmarkSuiteRow, row.suite_pk)
    if suite_row is None:  # guaranteed by the foreign key
        raise NotFoundError(f"suite pk {row.suite_pk} not found")
    scenario_slugs = session.scalars(
        select(BenchmarkScenarioRow.slug)
        .join(ExperimentScenarioRow, ExperimentScenarioRow.scenario_pk == BenchmarkScenarioRow.pk)
        .where(ExperimentScenarioRow.experiment_id == experiment_id)
        .order_by(ExperimentScenarioRow.position)
    ).all()
    return Experiment(
        id=row.id,
        name=row.name,
        description=row.description,
        suite_id=suite_row.slug,
        suite_version=suite_row.version,
        agent_version_id=row.agent_version_id,
        scenario_ids=list(scenario_slugs),
        repetitions=row.repetitions,
        seed=row.seed,
        status=row.status,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def update_experiment_state(session: Session, experiment: Experiment) -> None:
    """Persist a (validated) status transition produced with ``Experiment.updated``."""
    result = session.execute(
        update(ExperimentRow)
        .where(ExperimentRow.id == experiment.id)
        .values(
            status=experiment.status,
            started_at=experiment.started_at,
            finished_at=experiment.finished_at,
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise NotFoundError(f"experiment '{experiment.id}' not found")


# --- runs and traces --------------------------------------------------------------------------


def _clean_text(value: str | None, policy: TracePayloadPolicy) -> str | None:
    if value is None:
        return None
    cleaned = sanitize(value, policy).value
    return cleaned if isinstance(cleaned, str) else str(cleaned)


def _clean_json(value: JsonValue, policy: TracePayloadPolicy) -> JsonValue:
    return sanitize(value, policy).value


def save_run(
    session: Session,
    result: ScenarioRunResult,
    trace: CollectedTrace,
    policy: TracePayloadPolicy = DEFAULT_POLICY,
) -> RunRow:
    run = result.run
    if (trace.run_id, trace.experiment_id) != (run.id, run.experiment_id):
        raise TraceMismatchError("trace belongs to a different run")
    if session.get(RunRow, run.id) is not None:
        raise DuplicateRunError(f"run '{run.id}' is already stored")
    experiment = session.get(ExperimentRow, run.experiment_id)
    if experiment is None:
        raise NotFoundError(f"experiment '{run.experiment_id}' not found")
    scenario = scenario_row(session, experiment.suite_pk, run.scenario_id)
    faults = fault_pks(session, scenario.pk)

    output = result.output
    violation = result.termination.violation
    limits = result.configuration.limits
    # Snapshot sizes are bounded by policy; give the configuration a larger budget.
    config_policy = policy.updated(max_payload_bytes=max(policy.max_payload_bytes, 262_144))
    row = RunRow(
        id=run.id,
        experiment_id=run.experiment_id,
        scenario_pk=scenario.pk,
        agent_version_id=run.agent_version_id,
        repetition=run.repetition,
        seed=run.seed,
        status=run.status,
        termination_reason=result.termination.reason,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_type=run.error.error_type if run.error else None,
        error_message=_clean_text(run.error.message if run.error else None, policy),
        limit_kind=violation.kind if violation else None,
        limit_value=violation.limit if violation else None,
        limit_observed=violation.observed if violation else None,
        max_steps=limits.max_steps,
        max_tool_calls=limits.max_tool_calls,
        deadline_seconds=limits.deadline_seconds,
        declared_outcome=output.answer.declared_outcome if output else None,
        final_answer=_clean_text(output.answer.content, policy) if output else None,
        answer_source_ids=list(output.answer.source_ids) if output else None,
        agent_metadata=_clean_json(output.metadata.model_dump(mode="json"), policy)
        if output
        else None,
        effective_configuration=_clean_json(
            result.configuration.model_dump(mode="json"), config_policy
        ),
        trace_format_version=trace.format_version,
        trace_event_count=len(trace.events),
    )
    session.add(row)
    session.flush()

    event_rows = []
    for event in trace.events:
        fault_pk = None
        if event.fault_id is not None:
            fault_pk = faults.get(event.fault_id)
            if fault_pk is None:
                raise TraceMismatchError(
                    f"event '{event.id}' references fault '{event.fault_id}', which is not "
                    f"configured on scenario '{run.scenario_id}'"
                )
        event_rows.append(_event_row(event, fault_pk))
    if event_rows:
        session.execute(insert(TraceEventRow), event_rows)
    session.flush()
    return row


def _event_row(event: TraceEventRecord, fault_pk: int | None) -> dict[str, object]:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "experiment_id": event.experiment_id,
        "parent_id": event.parent_id,
        "sequence": event.sequence,
        "kind": event.kind,
        "status": event.status,
        "started_at": event.started_at,
        "ended_at": event.ended_at,
        "tool_name": event.tool_name,
        "tool_call_id": event.tool_call_id,
        "arguments": event.arguments,
        "payload": event.payload,
        "payload_truncated": event.payload_truncated,
        "payload_original_bytes": event.payload_original_bytes,
        "error_type": event.error_type,
        "error_details": event.error_details,
        "agent_name": event.agent_name,
        "agent_version": event.agent_version,
        "model_provider": event.model_provider,
        "model_name": event.model_name,
        "fault_pk": fault_pk,
        "attributes": event.attributes,
    }


class StoredRun(DomainModel):
    """A run as read back from storage."""

    run: Run
    termination_reason: TerminationReason
    limit_kind: LimitKind | None
    declared_outcome: AgentOutcome | None
    final_answer: str | None
    answer_source_ids: list[str] | None
    effective_configuration: dict[str, JsonValue]
    trace_event_count: int
    trace_purged_at: datetime | None


def load_run(session: Session, run_id: str) -> StoredRun:
    row = session.get(RunRow, run_id)
    if row is None:
        raise NotFoundError(f"run '{run_id}' not found")
    scenario_slug, case_slug = session.execute(
        select(BenchmarkScenarioRow.slug, BenchmarkCaseRow.slug)
        .join(BenchmarkCaseRow, BenchmarkCaseRow.pk == BenchmarkScenarioRow.case_pk)
        .where(BenchmarkScenarioRow.pk == row.scenario_pk)
    ).one()
    run = Run(
        id=row.id,
        experiment_id=row.experiment_id,
        case_id=case_slug,
        scenario_id=scenario_slug,
        agent_version_id=row.agent_version_id,
        repetition=row.repetition,
        seed=row.seed,
        status=row.status,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=RunError(error_type=row.error_type, message=row.error_message or row.error_type)
        if row.error_type
        else None,
    )
    return StoredRun(
        run=run,
        termination_reason=row.termination_reason,
        limit_kind=row.limit_kind,
        declared_outcome=row.declared_outcome,
        final_answer=row.final_answer,
        answer_source_ids=row.answer_source_ids,
        effective_configuration=row.effective_configuration,
        trace_event_count=row.trace_event_count,
        trace_purged_at=row.trace_purged_at,
    )


def load_trace(session: Session, run_id: str) -> CollectedTrace:
    """Rebuild a run's trace from storage; ``CollectedTrace`` re-validates the whole tree."""
    run_row = session.get(RunRow, run_id)
    if run_row is None:
        raise NotFoundError(f"run '{run_id}' not found")
    if run_row.trace_purged_at is not None:
        raise NotFoundError(f"trace of run '{run_id}' was purged at {run_row.trace_purged_at}")
    rows = session.execute(
        select(TraceEventRow, ScenarioFaultRow.slug, ScenarioFaultRow.fault_type)
        .outerjoin(ScenarioFaultRow, ScenarioFaultRow.pk == TraceEventRow.fault_pk)
        .where(TraceEventRow.run_id == run_id)
        .order_by(TraceEventRow.sequence)
    ).all()
    events = [
        TraceEventRecord(
            id=event.id,
            experiment_id=event.experiment_id,
            run_id=event.run_id,
            parent_id=event.parent_id,
            sequence=event.sequence,
            kind=event.kind,
            status=event.status,
            started_at=event.started_at,
            ended_at=event.ended_at,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            arguments=event.arguments,
            payload=event.payload,
            payload_truncated=event.payload_truncated,
            payload_original_bytes=event.payload_original_bytes,
            error_type=event.error_type,
            error_details=event.error_details,
            agent_name=event.agent_name,
            agent_version=event.agent_version,
            model_provider=event.model_provider,
            model_name=event.model_name,
            fault_id=fault_slug,
            fault_type=fault_type,
            attributes=event.attributes,
        )
        for event, fault_slug, fault_type in rows
    ]
    if len(events) != run_row.trace_event_count:
        raise TraceMismatchError(
            f"run '{run_id}' expects {run_row.trace_event_count} events, found {len(events)}"
        )
    return CollectedTrace(
        format_version=run_row.trace_format_version,
        experiment_id=run_row.experiment_id,
        run_id=run_id,
        events=events,
    )


def list_run_ids(session: Session, experiment_id: str) -> list[RunId]:
    return list(
        session.scalars(
            select(RunRow.id)
            .where(RunRow.experiment_id == experiment_id)
            .order_by(RunRow.scenario_pk, RunRow.repetition)
        ).all()
    )


# --- retention ------------------------------------------------------------------------------


def purge_traces(session: Session, *, finished_before: datetime, now: datetime) -> int:
    """Delete trace events of runs that finished before ``finished_before``.

    Run rows (outcome, answer, configuration snapshot) are kept and marked ``trace_purged_at``.
    Runs with any trace event referenced by a human review or by failure evidence are skipped:
    their traces are retained. Returns the number of runs whose traces were purged.
    """
    referenced = exists().where(
        TraceEventRow.run_id == RunRow.id,
        exists().where(ReviewAnnotationRow.trace_event_id == TraceEventRow.id)
        | exists().where(FailureEvidenceRow.trace_event_id == TraceEventRow.id),
    )
    run_ids = list(
        session.scalars(
            select(RunRow.id).where(
                RunRow.finished_at < finished_before,
                RunRow.trace_purged_at.is_(None),
                ~referenced,
            )
        ).all()
    )
    if not run_ids:
        return 0
    session.execute(delete(TraceEventRow).where(TraceEventRow.run_id.in_(run_ids)))
    session.execute(update(RunRow).where(RunRow.id.in_(run_ids)).values(trace_purged_at=now))
    session.flush()
    return len(run_ids)


def purge_expired_traces(session: Session, *, retention_days: int, now: datetime) -> int:
    """Apply the retention policy: purge traces of runs that finished more than
    ``retention_days`` before ``now`` (see ``purge_traces`` for what is kept)."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    return purge_traces(session, finished_before=now - timedelta(days=retention_days), now=now)
