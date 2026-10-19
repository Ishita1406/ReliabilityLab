"""Persistence for benchmark suites (cases, scenarios, faults) and agent configurations.

Registration is idempotent: saving content that already exists returns the existing row; saving
*different* content under an existing natural key raises ``ContentConflictError``, because suite
versions and agent versions are immutable once runs may reference them.
"""

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentConfigRow,
    AgentVersionRow,
    BenchmarkCaseRow,
    BenchmarkScenarioRow,
    BenchmarkSuiteRow,
    ScenarioFaultRow,
)
from app.domain.agent import AgentConfig, AgentParameters, AgentVersion, ModelSpec
from app.domain.benchmark import BenchmarkSuite
from app.domain.faults import FaultConfig
from app.domain.hashing import content_sha256

_FAULT = TypeAdapter(FaultConfig)
_FAULT_COMMON_FIELDS = {"id", "type", "target_tool", "apply_on_calls", "probability"}


class ContentConflictError(Exception):
    """Different content was saved under an existing immutable natural key."""


class NotFoundError(LookupError):
    """A referenced row does not exist."""


# --- suites ---------------------------------------------------------------------------------


def find_suite_row(session: Session, slug: str, version: str) -> BenchmarkSuiteRow | None:
    return session.scalar(
        select(BenchmarkSuiteRow).where(
            BenchmarkSuiteRow.slug == slug, BenchmarkSuiteRow.version == version
        )
    )


def save_suite(session: Session, suite: BenchmarkSuite) -> BenchmarkSuiteRow:
    digest = content_sha256(suite)
    existing = find_suite_row(session, suite.id, suite.version)
    if existing is not None:
        if existing.content_sha256 != digest:
            raise ContentConflictError(
                f"suite '{suite.id}' version '{suite.version}' already exists with different "
                "content; publish a new version instead"
            )
        return existing

    suite_row = BenchmarkSuiteRow(
        slug=suite.id,
        version=suite.version,
        schema_version=suite.schema_version,
        name=suite.name,
        description=suite.description,
        synthetic=suite.synthetic,
        created_at=suite.created_at,
        content_sha256=digest,
    )
    session.add(suite_row)
    session.flush()

    case_pks: dict[str, int] = {}
    for position, case in enumerate(suite.cases):
        case_row = BenchmarkCaseRow(
            suite_pk=suite_row.pk,
            position=position,
            slug=case.id,
            title=case.title,
            task_description=case.task_description,
            difficulty=case.difficulty,
            tags=list(case.tags),
            synthetic=case.metadata.synthetic,
            inputs=case.inputs.model_dump(mode="json"),
            expectation=case.expectation.model_dump(mode="json"),
            case_metadata=case.metadata.model_dump(mode="json"),
        )
        session.add(case_row)
        session.flush()
        case_pks[case.id] = case_row.pk

    for position, scenario in enumerate(suite.scenarios):
        override = scenario.expectation_override
        scenario_row = BenchmarkScenarioRow(
            suite_pk=suite_row.pk,
            case_pk=case_pks[scenario.case_id],
            position=position,
            slug=scenario.id,
            kind=scenario.kind,
            description=scenario.description,
            expectation_override=override.model_dump(mode="json") if override else None,
        )
        session.add(scenario_row)
        session.flush()
        for fault_position, fault in enumerate(scenario.faults):
            session.add(
                ScenarioFaultRow(
                    scenario_pk=scenario_row.pk,
                    position=fault_position,
                    slug=fault.id,
                    fault_type=fault.type,
                    target_tool=fault.target_tool,
                    apply_on_calls=fault.apply_on_calls,
                    probability=fault.probability,
                    parameters=fault.model_dump(mode="json", exclude=_FAULT_COMMON_FIELDS),
                )
            )
    session.flush()
    return suite_row


def load_suite(session: Session, slug: str, version: str) -> BenchmarkSuite:
    suite_row = find_suite_row(session, slug, version)
    if suite_row is None:
        raise NotFoundError(f"suite '{slug}' version '{version}' not found")
    return load_suite_by_pk(session, suite_row.pk)


def load_suite_by_pk(session: Session, suite_pk: int) -> BenchmarkSuite:
    suite_row = session.get(BenchmarkSuiteRow, suite_pk)
    if suite_row is None:
        raise NotFoundError(f"suite pk {suite_pk} not found")
    cases = session.scalars(
        select(BenchmarkCaseRow)
        .where(BenchmarkCaseRow.suite_pk == suite_pk)
        .order_by(BenchmarkCaseRow.position)
    ).all()
    case_slugs = {row.pk: row.slug for row in cases}
    scenarios = session.scalars(
        select(BenchmarkScenarioRow)
        .where(BenchmarkScenarioRow.suite_pk == suite_pk)
        .order_by(BenchmarkScenarioRow.position)
    ).all()
    faults = session.scalars(
        select(ScenarioFaultRow)
        .where(ScenarioFaultRow.scenario_pk.in_([s.pk for s in scenarios]))
        .order_by(ScenarioFaultRow.scenario_pk, ScenarioFaultRow.position)
    ).all()
    faults_by_scenario: dict[int, list[ScenarioFaultRow]] = {}
    for fault_row in faults:
        faults_by_scenario.setdefault(fault_row.scenario_pk, []).append(fault_row)

    return BenchmarkSuite.model_validate(
        {
            "schema_version": suite_row.schema_version,
            "id": suite_row.slug,
            "version": suite_row.version,
            "name": suite_row.name,
            "description": suite_row.description,
            "synthetic": suite_row.synthetic,
            "created_at": suite_row.created_at,
            "cases": [
                {
                    "id": row.slug,
                    "title": row.title,
                    "task_description": row.task_description,
                    "inputs": row.inputs,
                    "expectation": row.expectation,
                    "tags": row.tags,
                    "difficulty": row.difficulty,
                    "metadata": row.case_metadata,
                }
                for row in cases
            ],
            "scenarios": [
                {
                    "id": row.slug,
                    "case_id": case_slugs[row.case_pk],
                    "kind": row.kind,
                    "description": row.description,
                    "faults": [
                        fault_from_row(f).model_dump(mode="json")
                        for f in faults_by_scenario.get(row.pk, [])
                    ],
                    "expectation_override": row.expectation_override,
                }
                for row in scenarios
            ],
        }
    )


def fault_from_row(row: ScenarioFaultRow) -> FaultConfig:
    return _FAULT.validate_python(
        {
            **row.parameters,
            "id": row.slug,
            "type": row.fault_type,
            "target_tool": row.target_tool,
            "apply_on_calls": row.apply_on_calls,
            "probability": row.probability,
        }
    )


def scenario_row(session: Session, suite_pk: int, scenario_slug: str) -> BenchmarkScenarioRow:
    row = session.scalar(
        select(BenchmarkScenarioRow).where(
            BenchmarkScenarioRow.suite_pk == suite_pk, BenchmarkScenarioRow.slug == scenario_slug
        )
    )
    if row is None:
        raise NotFoundError(f"scenario '{scenario_slug}' not found in suite pk {suite_pk}")
    return row


def fault_pks(session: Session, scenario_pk: int) -> dict[str, int]:
    rows = session.execute(
        select(ScenarioFaultRow.slug, ScenarioFaultRow.pk).where(
            ScenarioFaultRow.scenario_pk == scenario_pk
        )
    ).all()
    return {slug: pk for slug, pk in rows}


# --- agents ---------------------------------------------------------------------------------


def save_agent(session: Session, config: AgentConfig, version: AgentVersion) -> AgentVersionRow:
    if version.agent_config_id != config.id:
        raise ValueError("agent version does not belong to this agent config")

    config_row = session.get(AgentConfigRow, config.id)
    if config_row is None:
        session.add(
            AgentConfigRow(
                id=config.id,
                name=config.name,
                description=config.description,
                created_at=config.created_at,
            )
        )
    elif (config_row.name, config_row.description) != (config.name, config.description):
        raise ContentConflictError(f"agent config '{config.id}' already exists with other data")

    digest = content_sha256(version)
    version_row = session.get(AgentVersionRow, version.id)
    if version_row is not None:
        if version_row.content_sha256 != digest:
            raise ContentConflictError(
                f"agent version '{version.id}' already exists with different content"
            )
        return version_row

    version_row = AgentVersionRow(
        id=version.id,
        agent_config_id=version.agent_config_id,
        version_label=version.version_label,
        model_provider=version.model.provider,
        model_name=version.model.name,
        system_prompt=version.system_prompt,
        tools=list(version.tools),
        temperature=version.parameters.temperature,
        max_steps=version.parameters.max_steps,
        timeout_seconds=version.parameters.timeout_seconds,
        created_at=version.created_at,
        content_sha256=digest,
    )
    session.add(version_row)
    session.flush()
    return version_row


def load_agent_version(session: Session, version_id: str) -> AgentVersion:
    row = session.get(AgentVersionRow, version_id)
    if row is None:
        raise NotFoundError(f"agent version '{version_id}' not found")
    return AgentVersion(
        id=row.id,
        agent_config_id=row.agent_config_id,
        version_label=row.version_label,
        model=ModelSpec(provider=row.model_provider, name=row.model_name),
        system_prompt=row.system_prompt,
        tools=list(row.tools),
        parameters=AgentParameters(
            temperature=row.temperature,
            max_steps=row.max_steps,
            timeout_seconds=row.timeout_seconds,
        ),
        created_at=row.created_at,
    )
