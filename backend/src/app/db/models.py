"""Relational schema (SQLAlchemy 2.x, PostgreSQL).

Conventions
-----------
* Runtime entities keep their domain IDs (``run_<hex>``, ...) as ``TEXT`` primary keys, with a
  CHECK on the prefix pattern. Benchmark content (suites, cases, scenarios, faults) uses
  surrogate ``BIGINT`` identity keys plus explicit natural-key uniqueness constraints
  (``(slug, version)``, ``(suite_pk, slug)``, ...), because slugs are only unique in scope.
* Enumerations are ``VARCHAR`` + CHECK constraints (``native_enum=False``), not PostgreSQL enum
  types, so adding a value is a constraint change rather than an ``ALTER TYPE``.
* Cross-table consistency is enforced with composite foreign keys where it matters:
  a scenario's case is in the same suite; an experiment's scenarios are in its suite; a run's
  scenario is one of its experiment's scenarios and its agent version is the experiment's;
  a trace event's experiment is its run's, and its parent is in the same run; a failure
  classification's evaluation is for the same run.
* JSONB holds genuinely flexible content (task inputs, expectations, agent metadata, sanitised
  tool payloads, configuration snapshots); relationships are always columns + foreign keys.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.enums import (
    AgentOutcome,
    ClassificationSource,
    CriterionType,
    Difficulty,
    ExperimentStatus,
    FailureCategory,
    FaultType,
    ReviewTargetType,
    ReviewVerdict,
    RunStatus,
    ScenarioKind,
    Severity,
)
from app.domain.ids import SLUG_PATTERN
from app.evaluation.models import Verdict
from app.review.models import ReviewLabel
from app.runner.runner import TerminationReason
from app.runtime.limits import LimitKind
from app.tracing.schema import EventKind, EventStatus

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


NullableJSONB = JSONB(none_as_null=True)
"""Python ``None`` is stored as SQL NULL (absent), never as the JSON value ``null``."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def enum_type(enum: type[StrEnum], name: str) -> SAEnum:
    """VARCHAR column mapped to a Python enum. The allowed values are enforced by an explicit
    ``enum_check`` in the table args (``create_constraint=False``): SQLAlchemy's implicit enum
    constraint makes Alembic autogenerate render duplicate CHECK constraints."""
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=40,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def enum_check(column: str, enum: type[StrEnum]) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_values")


def _id_check(column: str, prefix: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^{prefix}_[0-9a-f]{{32}}$'", name=f"{column}_format")


def _slug_check(column: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '{SLUG_PATTERN}'", name=f"{column}_format")


Created = Mapped[datetime]


# --- benchmark content -----------------------------------------------------------------------


class BenchmarkSuiteRow(Base):
    __tablename__ = "benchmark_suites"
    __table_args__ = (
        UniqueConstraint("slug", "version"),
        _slug_check("slug"),
    )

    pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    synthetic: Mapped[bool] = mapped_column(Boolean)
    created_at: Created
    content_sha256: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BenchmarkCaseRow(Base):
    __tablename__ = "benchmark_cases"
    __table_args__ = (
        enum_check("difficulty", Difficulty),
        UniqueConstraint("suite_pk", "slug"),
        UniqueConstraint("suite_pk", "position"),
        UniqueConstraint("suite_pk", "pk"),  # target of composite FKs
        _slug_check("slug"),
        Index("ix_benchmark_cases_tags", "tags", postgresql_using="gin"),
    )

    pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    suite_pk: Mapped[int] = mapped_column(
        ForeignKey("benchmark_suites.pk", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    task_description: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(enum_type(Difficulty, "difficulty"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    synthetic: Mapped[bool] = mapped_column(Boolean)
    inputs: Mapped[dict[str, Any]]
    expectation: Mapped[dict[str, Any]]
    case_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)


class BenchmarkScenarioRow(Base):
    __tablename__ = "benchmark_scenarios"
    __table_args__ = (
        enum_check("kind", ScenarioKind),
        UniqueConstraint("suite_pk", "slug"),
        UniqueConstraint("suite_pk", "position"),
        UniqueConstraint("suite_pk", "pk"),  # target of composite FKs
        ForeignKeyConstraint(
            ["suite_pk", "case_pk"],
            ["benchmark_cases.suite_pk", "benchmark_cases.pk"],
            ondelete="CASCADE",
            name="fk_benchmark_scenarios_case_in_suite",
        ),
        CheckConstraint(
            "kind <> 'baseline' OR expectation_override IS NULL", name="baseline_no_override"
        ),
        Index(
            "uq_benchmark_scenarios_one_baseline_per_case",
            "case_pk",
            unique=True,
            postgresql_where=text("kind = 'baseline'"),
        ),
        _slug_check("slug"),
    )

    pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    suite_pk: Mapped[int] = mapped_column(
        ForeignKey("benchmark_suites.pk", ondelete="CASCADE"), index=True
    )
    case_pk: Mapped[int] = mapped_column(BigInteger, index=True)
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(Text)
    kind: Mapped[ScenarioKind] = mapped_column(enum_type(ScenarioKind, "scenario_kind"))
    description: Mapped[str] = mapped_column(Text)
    expectation_override: Mapped[dict[str, Any] | None] = mapped_column(NullableJSONB)


class ScenarioFaultRow(Base):
    __tablename__ = "scenario_faults"
    __table_args__ = (
        enum_check("fault_type", FaultType),
        UniqueConstraint("scenario_pk", "slug"),
        UniqueConstraint("scenario_pk", "position"),
        CheckConstraint("probability >= 0 AND probability <= 1", name="probability_range"),
        _slug_check("slug"),
    )

    pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scenario_pk: Mapped[int] = mapped_column(
        ForeignKey("benchmark_scenarios.pk", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    slug: Mapped[str] = mapped_column(Text)
    fault_type: Mapped[FaultType] = mapped_column(enum_type(FaultType, "fault_type"))
    target_tool: Mapped[str] = mapped_column(Text)
    apply_on_calls: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    probability: Mapped[float] = mapped_column(Double)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, comment="Type-specific fault parameters (e.g. timeout_ms)."
    )


# --- agents -----------------------------------------------------------------------------------


class AgentConfigRow(Base):
    __tablename__ = "agent_configs"
    __table_args__ = (_id_check("id", "agt"), UniqueConstraint("name"))

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Created


class AgentVersionRow(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        _id_check("id", "agv"),
        UniqueConstraint("agent_config_id", "version_label"),
        CheckConstraint("max_steps > 0 AND timeout_seconds > 0", name="positive_limits"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_config_id: Mapped[str] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="RESTRICT"), index=True
    )
    version_label: Mapped[str] = mapped_column(Text)
    model_provider: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    tools: Mapped[list[str]] = mapped_column(ARRAY(Text))
    temperature: Mapped[float | None] = mapped_column(Double)
    max_steps: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[float] = mapped_column(Double)
    created_at: Created
    content_sha256: Mapped[str] = mapped_column(String(64))


# --- experiments and runs ---------------------------------------------------------------------


class ExperimentRow(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        enum_check("status", ExperimentStatus),
        _id_check("id", "exp"),
        UniqueConstraint("id", "suite_pk"),  # target of composite FKs
        UniqueConstraint("id", "agent_version_id"),  # target of composite FKs
        CheckConstraint("repetitions >= 1 AND seed >= 0", name="positive_settings"),
        CheckConstraint(
            "(configuration IS NULL) = (configuration_sha256 IS NULL) AND "
            "(configuration IS NULL) = (evaluator_version IS NULL)",
            name="configuration_complete",
        ),
        CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) AND "
            "(finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at))",
            name="timestamp_order",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    suite_pk: Mapped[int] = mapped_column(
        ForeignKey("benchmark_suites.pk", ondelete="RESTRICT"), index=True
    )
    agent_version_id: Mapped[str] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT"), index=True
    )
    repetitions: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[ExperimentStatus] = mapped_column(
        enum_type(ExperimentStatus, "experiment_status"), index=True
    )
    created_at: Created
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    # Milestone 7: immutable configuration snapshot (NULL for experiments created before it).
    evaluator_version: Mapped[str | None] = mapped_column(Text)
    configuration: Mapped[dict[str, Any] | None] = mapped_column(
        NullableJSONB, comment="ExperimentConfiguration snapshot (app.experiments.configuration)."
    )
    configuration_sha256: Mapped[str | None] = mapped_column(String(64))


class ExperimentScenarioRow(Base):
    __tablename__ = "experiment_scenarios"
    __table_args__ = (
        UniqueConstraint("experiment_id", "position"),
        ForeignKeyConstraint(
            ["experiment_id", "suite_pk"],
            ["experiments.id", "experiments.suite_pk"],
            ondelete="CASCADE",
            name="fk_experiment_scenarios_experiment_suite",
        ),
        ForeignKeyConstraint(
            ["suite_pk", "scenario_pk"],
            ["benchmark_scenarios.suite_pk", "benchmark_scenarios.pk"],
            ondelete="RESTRICT",
            name="fk_experiment_scenarios_scenario_in_suite",
        ),
    )

    experiment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    scenario_pk: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    suite_pk: Mapped[int] = mapped_column(BigInteger)
    position: Mapped[int] = mapped_column(Integer)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        enum_check("status", RunStatus),
        enum_check("termination_reason", TerminationReason),
        enum_check("limit_kind", LimitKind),
        enum_check("declared_outcome", AgentOutcome),
        _id_check("id", "run"),
        UniqueConstraint("experiment_id", "scenario_pk", "repetition"),
        UniqueConstraint("id", "experiment_id"),  # target of the trace_events composite FK
        ForeignKeyConstraint(
            ["experiment_id", "scenario_pk"],
            ["experiment_scenarios.experiment_id", "experiment_scenarios.scenario_pk"],
            ondelete="CASCADE",
            name="fk_runs_scenario_in_experiment",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "agent_version_id"],
            ["experiments.id", "experiments.agent_version_id"],
            ondelete="CASCADE",
            name="fk_runs_agent_version_of_experiment",
        ),
        CheckConstraint(
            "(status = 'completed') = (declared_outcome IS NOT NULL AND final_answer IS NOT NULL)",
            name="answer_iff_completed",
        ),
        CheckConstraint(
            "(status <> 'errored' OR error_type IS NOT NULL) AND "
            "(error_type IS NULL OR status IN ('errored', 'timed_out', 'limit_exceeded'))",
            name="error_matches_status",
        ),
        CheckConstraint(
            "(limit_kind IS NULL) = (status NOT IN ('timed_out', 'limit_exceeded'))",
            name="limit_iff_limit_status",
        ),
        CheckConstraint(
            "repetition >= 0 AND seed >= 0 AND trace_event_count >= 0", name="non_negative"
        ),
        CheckConstraint(
            "(started_at IS NULL OR started_at >= created_at) AND "
            "(finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at))",
            name="timestamp_order",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(Text, index=True)
    scenario_pk: Mapped[int] = mapped_column(BigInteger, index=True)
    agent_version_id: Mapped[str] = mapped_column(Text, index=True)
    repetition: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus, "run_status"), index=True)
    termination_reason: Mapped[TerminationReason] = mapped_column(
        enum_type(TerminationReason, "termination_reason")
    )
    created_at: Created
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None] = mapped_column(index=True)
    error_type: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    limit_kind: Mapped[LimitKind | None] = mapped_column(enum_type(LimitKind, "limit_kind"))
    limit_value: Mapped[float | None] = mapped_column(Double)
    limit_observed: Mapped[float | None] = mapped_column(Double)
    max_steps: Mapped[int] = mapped_column(Integer)
    max_tool_calls: Mapped[int] = mapped_column(Integer)
    deadline_seconds: Mapped[float] = mapped_column(Double)
    declared_outcome: Mapped[AgentOutcome | None] = mapped_column(
        enum_type(AgentOutcome, "agent_outcome")
    )
    final_answer: Mapped[str | None] = mapped_column(Text)
    answer_source_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    agent_metadata: Mapped[dict[str, Any] | None] = mapped_column(NullableJSONB)
    effective_configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, comment="Sanitised snapshot of EffectiveRunConfiguration (reproducibility record)."
    )
    trace_format_version: Mapped[str] = mapped_column(Text)
    trace_event_count: Mapped[int] = mapped_column(Integer)
    trace_purged_at: Mapped[datetime | None]


class TraceEventRow(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        enum_check("kind", EventKind),
        enum_check("status", EventStatus),
        _id_check("id", "evt"),
        UniqueConstraint("run_id", "sequence"),
        UniqueConstraint("run_id", "id"),  # target of the parent composite FK
        ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["runs.id", "runs.experiment_id"],
            ondelete="CASCADE",
            name="fk_trace_events_run_experiment",
        ),
        ForeignKeyConstraint(
            ["run_id", "parent_id"],
            ["trace_events.run_id", "trace_events.id"],
            ondelete="CASCADE",
            name="fk_trace_events_parent_in_run",
        ),
        CheckConstraint("ended_at >= started_at", name="time_order"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint("(kind = 'run') = (parent_id IS NULL)", name="root_iff_run"),
        CheckConstraint("char_length(tool_name) <= 200", name="tool_name_length"),
        Index(
            "ix_trace_events_tool_call_id",
            "tool_call_id",
            postgresql_where=text("tool_call_id IS NOT NULL"),
        ),
        Index(
            "ix_trace_events_fault_pk", "fault_pk", postgresql_where=text("fault_pk IS NOT NULL")
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text)
    experiment_id: Mapped[str] = mapped_column(Text, index=True)
    parent_id: Mapped[str | None] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[EventKind] = mapped_column(enum_type(EventKind, "event_kind"), index=True)
    status: Mapped[EventStatus] = mapped_column(enum_type(EventStatus, "event_status"))
    started_at: Created
    ended_at: Created
    duration_ms: Mapped[float] = mapped_column(
        Double, Computed("EXTRACT(EPOCH FROM (ended_at - started_at)) * 1000", persisted=True)
    )
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_call_id: Mapped[str | None] = mapped_column(Text)
    arguments: Mapped[Any] = mapped_column(NullableJSONB, nullable=True)
    payload: Mapped[Any] = mapped_column(NullableJSONB, nullable=True)
    payload_truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    payload_original_bytes: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[Any] = mapped_column(NullableJSONB, nullable=True)
    agent_name: Mapped[str | None] = mapped_column(Text)
    agent_version: Mapped[str | None] = mapped_column(Text)
    model_provider: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    fault_pk: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_faults.pk", ondelete="RESTRICT")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


# --- evaluation and review -------------------------------------------------------------------


class RunEvaluationRow(Base):
    """A run's Milestone 6 ``CaseEvaluation`` (full document) per evaluator version."""

    __tablename__ = "run_evaluations"
    __table_args__ = (
        enum_check("verdict", Verdict),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        ForeignKeyConstraint(
            ["run_id", "experiment_id"],
            ["runs.id", "runs.experiment_id"],
            ondelete="CASCADE",
            name="fk_run_evaluations_run_of_experiment",
        ),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    evaluator_version: Mapped[str] = mapped_column(Text, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(Text, index=True)
    verdict: Mapped[Verdict] = mapped_column(enum_type(Verdict, "verdict"), index=True)
    score: Mapped[float] = mapped_column(Double)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Created


class EvaluationResultRow(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        _id_check("id", "evr"),
        UniqueConstraint("run_id", "evaluator_version"),
        UniqueConstraint("id", "run_id"),  # target of composite FKs
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("pass_threshold > 0 AND pass_threshold <= 1", name="threshold_range"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    evaluator_version: Mapped[str] = mapped_column(Text)
    pass_threshold: Mapped[float] = mapped_column(Double)
    score: Mapped[float] = mapped_column(Double)
    passed: Mapped[bool] = mapped_column(Boolean)
    created_at: Created


class CriterionResultRow(Base):
    __tablename__ = "criterion_results"
    __table_args__ = (
        enum_check("criterion_type", CriterionType),
        CheckConstraint("weight > 0", name="positive_weight"),
    )

    evaluation_result_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_results.id", ondelete="CASCADE"), primary_key=True
    )
    criterion_slug: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    criterion_type: Mapped[CriterionType] = mapped_column(
        enum_type(CriterionType, "criterion_type")
    )
    passed: Mapped[bool] = mapped_column(Boolean)
    weight: Mapped[float] = mapped_column(Double)
    required: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)


class FailureClassificationRow(Base):
    __tablename__ = "failure_classifications"
    __table_args__ = (
        enum_check("category", FailureCategory),
        enum_check("severity", Severity),
        enum_check("source", ClassificationSource),
        _id_check("id", "fcl"),
        ForeignKeyConstraint(
            ["evaluation_result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            ondelete="CASCADE",
            name="fk_failure_classifications_evaluation_of_run",
        ),
        CheckConstraint(
            "source <> 'rule' OR evaluation_result_id IS NOT NULL", name="rule_needs_evaluation"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    evaluation_result_id: Mapped[str | None] = mapped_column(Text, index=True)
    category: Mapped[FailureCategory] = mapped_column(
        enum_type(FailureCategory, "failure_category"), index=True
    )
    severity: Mapped[Severity] = mapped_column(enum_type(Severity, "severity"))
    source: Mapped[ClassificationSource] = mapped_column(
        enum_type(ClassificationSource, "classification_source")
    )
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Created


class FailureEvidenceRow(Base):
    __tablename__ = "failure_classification_evidence"

    classification_id: Mapped[str] = mapped_column(
        ForeignKey("failure_classifications.id", ondelete="CASCADE"), primary_key=True
    )
    # NO ACTION (checked at statement end): purging or deleting a trace event that is cited
    # as evidence is refused unless the classification is deleted in the same statement.
    trace_event_id: Mapped[str] = mapped_column(
        ForeignKey("trace_events.id"), primary_key=True, index=True
    )
    position: Mapped[int] = mapped_column(Integer)


class ReviewAnnotationRow(Base):
    """Human annotations. Their foreign keys use NO ACTION: deleting or purging anything a human
    has reviewed is refused by the database instead of silently deleting the review."""

    __tablename__ = "review_annotations"
    __table_args__ = (
        enum_check("target_type", ReviewTargetType),
        enum_check("verdict", ReviewVerdict),
        enum_check("corrected_failure_category", FailureCategory),
        _id_check("id", "rev"),
        CheckConstraint(
            "num_nonnulls(run_id, evaluation_result_id, failure_classification_id, "
            "trace_event_id) = 1 AND "
            "(target_type <> 'run' OR run_id IS NOT NULL) AND "
            "(target_type <> 'evaluation_result' OR evaluation_result_id IS NOT NULL) AND "
            "(target_type <> 'failure_classification' OR failure_classification_id IS NOT NULL) "
            "AND (target_type <> 'trace_event' OR trace_event_id IS NOT NULL)",
            name="single_matching_target",
        ),
        CheckConstraint("updated_at IS NULL OR updated_at >= created_at", name="timestamp_order"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    target_type: Mapped[ReviewTargetType] = mapped_column(
        enum_type(ReviewTargetType, "review_target_type")
    )
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    evaluation_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_results.id"), index=True
    )
    failure_classification_id: Mapped[str | None] = mapped_column(
        ForeignKey("failure_classifications.id"), index=True
    )
    trace_event_id: Mapped[str | None] = mapped_column(ForeignKey("trace_events.id"), index=True)
    reviewer: Mapped[str] = mapped_column(Text)
    verdict: Mapped[ReviewVerdict] = mapped_column(enum_type(ReviewVerdict, "review_verdict"))
    comment: Mapped[str | None] = mapped_column(Text)
    corrected_failure_category: Mapped[FailureCategory | None] = mapped_column(
        enum_type(FailureCategory, "corrected_failure_category")
    )
    corrected_passed: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Created
    updated_at: Mapped[datetime | None]


class HumanReviewRow(Base):
    """Milestone 8 human reviews. Append-only: corrections are new rows that supersede the
    reviewer's previous row; a trigger (migration 0004) rejects UPDATE and DELETE."""

    __tablename__ = "human_reviews"
    __table_args__ = (
        _id_check("id", "hrv"),
        enum_check("label", ReviewLabel),
        CheckConstraint("provenance IN ('human', 'synthetic')", name="provenance_values"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint(
            "(supersedes IS NULL) = (correction_reason IS NULL)", name="correction_complete"
        ),
        CheckConstraint(
            "label IN ('pass', 'fail') OR rationale IS NOT NULL",
            name="non_definite_needs_rationale",
        ),
        CheckConstraint("supersedes IS NULL OR supersedes <> id", name="not_self_superseding"),
        UniqueConstraint("supersedes"),  # no forks: a review is corrected at most once
        UniqueConstraint("id", "run_id", "evaluator_version", "reviewer"),
        ForeignKeyConstraint(
            ["supersedes", "run_id", "evaluator_version", "reviewer"],
            [
                "human_reviews.id",
                "human_reviews.run_id",
                "human_reviews.evaluator_version",
                "human_reviews.reviewer",
            ],
            name="fk_human_reviews_supersedes_same_chain",
        ),
        ForeignKeyConstraint(
            ["run_id", "evaluator_version"],
            ["run_evaluations.run_id", "run_evaluations.evaluator_version"],
            name="fk_human_reviews_evaluation",
        ),
        Index(
            "uq_human_reviews_one_chain_root",
            "run_id",
            "evaluator_version",
            "reviewer",
            unique=True,
            postgresql_where=text("supersedes IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, index=True)
    evaluator_version: Mapped[str] = mapped_column(Text)
    case_id: Mapped[str] = mapped_column(Text)
    scenario_id: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(Text, index=True)
    label: Mapped[ReviewLabel] = mapped_column(enum_type(ReviewLabel, "review_label"))
    categories: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Double)
    evidence: Mapped[list[Any]] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)
    blind: Mapped[bool] = mapped_column(Boolean)
    provenance: Mapped[str] = mapped_column(Text)
    supersedes: Mapped[str | None] = mapped_column(Text)
    correction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Created
