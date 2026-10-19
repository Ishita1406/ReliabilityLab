"""Benchmark suites, cases, scenarios, expected outcomes, and deterministic scoring criteria.

A ``BenchmarkSuite`` is the unit that is versioned and stored as one dataset file. It contains
``BenchmarkCase`` records (the tasks) and ``Scenario`` records (ways of running a task). Every
case has exactly one baseline scenario and any number of fault-injected scenarios.

A case's ``Expectation`` (expected behaviour, criteria, scoring policy) applies to all of its
scenarios unless a fault-injected scenario supplies a complete ``expectation_override``, e.g.
when a permanently failing tool makes "answer the question" the wrong expectation.
"""

import re
from collections import Counter
from typing import Annotated, Literal, Self

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, JsonValue, NonNegativeInt, PositiveInt, model_validator

from app.domain.base import DomainModel, NonEmptyStr, SemVer, require_unique
from app.domain.enums import (
    AgentOutcome,
    AnswerFormat,
    CriterionType,
    Difficulty,
    MatchMode,
    Normalization,
    RecoveryBehavior,
    ScenarioKind,
)
from app.domain.faults import FaultConfig
from app.domain.ids import Slug
from app.domain.timestamps import UtcDateTime

SCHEMA_VERSION = "1.0"
"""Version of the dataset file format. Bump when the shape of these models changes."""

CITATION_PATTERN = r"\[source:([a-z0-9]+(?:[.\-_][a-z0-9]+)*)\]"
"""How agents cite a source document in their answer, e.g. ``[source:veloria-almanac]``."""

ScalarValue = str | int | float | bool

# --- Evaluation criteria -------------------------------------------------------------------


class _CriterionBase(DomainModel):
    id: Slug = Field(description="Identifier unique within its expectation.")
    description: NonEmptyStr
    weight: float = Field(default=1.0, gt=0.0, description="Relative weight in the score.")
    required: bool = Field(
        default=False, description="If true, the run fails when this criterion fails."
    )


class AnswerContainsCriterion(_CriterionBase):
    """The final answer contains the given strings (all of them, or any one of them)."""

    type: Literal[CriterionType.ANSWER_CONTAINS]
    values: list[NonEmptyStr] = Field(min_length=1)
    match: MatchMode = MatchMode.ALL
    case_sensitive: bool = False


class AnswerExcludesCriterion(_CriterionBase):
    """The final answer contains none of the given strings."""

    type: Literal[CriterionType.ANSWER_EXCLUDES]
    values: list[NonEmptyStr] = Field(min_length=1)
    case_sensitive: bool = False


class AnswerMatchesRegexCriterion(_CriterionBase):
    """The final answer matches a Python regular expression (``re.search`` semantics)."""

    type: Literal[CriterionType.ANSWER_MATCHES_REGEX]
    pattern: NonEmptyStr

    @model_validator(mode="after")
    def _pattern_compiles(self) -> Self:
        try:
            re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return self


class DeclaredOutcomeCriterion(_CriterionBase):
    """The agent declared this terminal outcome in its final answer."""

    type: Literal[CriterionType.DECLARED_OUTCOME]
    expected: AgentOutcome


class ToolCallCountCriterion(_CriterionBase):
    """The named tool was called between ``min_calls`` and ``max_calls`` times (inclusive)."""

    type: Literal[CriterionType.TOOL_CALL_COUNT]
    tool_name: Slug
    min_calls: NonNegativeInt = 0
    max_calls: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _bounds_are_consistent(self) -> Self:
        if self.max_calls is None and self.min_calls == 0:
            raise ValueError(
                "set min_calls > 0 or max_calls; otherwise the criterion always passes"
            )
        if self.max_calls is not None and self.max_calls < self.min_calls:
            raise ValueError("max_calls must be >= min_calls")
        return self


class CitationsWithinCriterion(_CriterionBase):
    """Every ``[source:<id>]`` citation in the answer refers to an allowed source document,
    and at least ``min_citations`` citations are present."""

    type: Literal[CriterionType.CITATIONS_WITHIN]
    allowed_source_ids: list[Slug] = Field(min_length=1)
    min_citations: NonNegativeInt = Field(default=1, description="Distinct cited source IDs.")
    require_tool_provenance: bool = Field(
        default=False,
        description="Every cited ID must appear in a successful tool result of the run.",
    )


class AnswerExactCriterion(_CriterionBase):
    """The (normalised) answer, or the JSON value at ``json_pointer``, equals one of the
    accepted values. Only appropriate for short or structured answers."""

    type: Literal[CriterionType.ANSWER_EXACT]
    accepted: list[NonEmptyStr] = Field(min_length=1)
    normalization: Normalization = Normalization.STANDARD
    json_pointer: str | None = Field(
        default=None,
        pattern=r"^(/[^/]*)*$",
        description="RFC 6901 pointer into a JSON answer (whole content); compared as text.",
    )


class TextMatcher(DomainModel):
    """Matches when any phrase (case-insensitive substring) or regex pattern
    (case-insensitive ``re.search``) occurs in the text."""

    id: Slug
    description: NonEmptyStr
    phrases: list[NonEmptyStr] = Field(default_factory=list)
    patterns: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_matchers(self) -> Self:
        if not self.phrases and not self.patterns:
            raise ValueError(f"'{self.id}' needs at least one phrase or pattern")
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"'{self.id}': invalid regular expression: {exc}") from exc
        return self


class ForbiddenClaim(TextMatcher):
    security_sensitive: bool = Field(
        default=False,
        description="True if asserting this claim indicates a security violation, e.g. "
        "compliance with injected instructions.",
    )


class RequiredFactsCriterion(_CriterionBase):
    """The answer states the listed facts (all of them, or at least ``min_present``)."""

    type: Literal[CriterionType.REQUIRED_FACTS]
    facts: list[TextMatcher] = Field(min_length=1)
    min_present: PositiveInt | None = Field(default=None, description="null means all facts.")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        require_unique((f.id for f in self.facts), "fact ids")
        if self.min_present is not None and self.min_present > len(self.facts):
            raise ValueError("min_present cannot exceed the number of facts")
        return self


class ForbiddenClaimsCriterion(_CriterionBase):
    """The answer asserts none of the listed claims (detected by text matching)."""

    type: Literal[CriterionType.FORBIDDEN_CLAIMS]
    claims: list[ForbiddenClaim] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique((c.id for c in self.claims), "claim ids")
        return self


class StructuredOutputCriterion(_CriterionBase):
    """The answer contains JSON that is valid against ``json_schema`` (Draft 2020-12)."""

    type: Literal[CriterionType.STRUCTURED_OUTPUT]
    json_schema: dict[str, JsonValue]
    answer_format: AnswerFormat = AnswerFormat.WHOLE

    @model_validator(mode="after")
    def _schema_valid(self) -> Self:
        try:
            Draft202012Validator.check_schema(self.json_schema)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON schema: {exc.message}") from exc
        return self


class RequiredToolCall(DomainModel):
    tool_name: Slug
    arguments: dict[str, ScalarValue] = Field(
        default_factory=dict, description="Arguments that must match exactly (a subset)."
    )
    must_succeed: bool = True
    min_count: PositiveInt = 1


class ToolCallsRequiredCriterion(_CriterionBase):
    """Each listed call was made (with matching arguments, successfully if required)."""

    type: Literal[CriterionType.TOOL_CALLS_REQUIRED]
    calls: list[RequiredToolCall] = Field(min_length=1)


class ProhibitedActionsCriterion(_CriterionBase):
    """No call or call attempt to a prohibited tool, and (by default) no request for a tool
    that the case does not make available."""

    type: Literal[CriterionType.PROHIBITED_ACTIONS]
    tool_names: list[NonEmptyStr] = Field(
        default_factory=list, description="Any string: prohibited tools need not exist."
    )
    forbid_unavailable_tools: bool = True

    @model_validator(mode="after")
    def _not_vacuous(self) -> Self:
        if not self.tool_names and not self.forbid_unavailable_tools:
            raise ValueError(
                "set tool_names or forbid_unavailable_tools; otherwise it always passes"
            )
        require_unique(self.tool_names, "tool_names")
        return self


class ExecutionLimitsCriterion(_CriterionBase):
    """The run completed without hitting a harness limit and stayed within these budgets."""

    type: Literal[CriterionType.EXECUTION_LIMITS]
    max_tool_calls: NonNegativeInt | None = None
    max_failed_tool_calls: NonNegativeInt | None = None
    max_steps: PositiveInt | None = None
    max_duration_seconds: float | None = Field(default=None, gt=0)


class ExpectedRecoveryCriterion(_CriterionBase):
    """The listed scenario faults fired, and the agent then behaved as ``behavior`` says.

    Only allowed in a fault-injected scenario's ``expectation_override`` (it refers to that
    scenario's faults)."""

    type: Literal[CriterionType.EXPECTED_RECOVERY]
    fault_ids: list[Slug] = Field(min_length=1)
    behavior: RecoveryBehavior
    max_attempts: PositiveInt | None = Field(
        default=None,
        description="Max identical calls (same tool and arguments) counting the faulted one.",
    )

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique(self.fault_ids, "fault_ids")
        return self


class RubricLevel(DomainModel):
    score: NonNegativeInt
    description: NonEmptyStr


class RubricDimension(DomainModel):
    id: Slug
    description: NonEmptyStr
    levels: list[RubricLevel] = Field(min_length=2)

    @model_validator(mode="after")
    def _levels_unique(self) -> Self:
        require_unique((str(level.score) for level in self.levels), f"'{self.id}' level scores")
        return self

    @property
    def min_score(self) -> int:
        return min(level.score for level in self.levels)

    @property
    def max_score(self) -> int:
        return max(level.score for level in self.levels)


class RubricCriterion(_CriterionBase):
    """Graded by a judge (e.g. an LLM) against the rubric. Without a judge, or when the
    judge is unreliable, the criterion is inconclusive rather than passed or failed."""

    type: Literal[CriterionType.RUBRIC]
    dimensions: list[RubricDimension] = Field(min_length=1)
    pass_score: float = Field(
        gt=0.0, le=1.0, description="Normalised score (0..1, mean over dimensions) to pass."
    )
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    guidance: NonEmptyStr | None = Field(default=None, description="Extra notes for the judge.")

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique((d.id for d in self.dimensions), "dimension ids")
        return self


EvaluationCriterion = Annotated[
    AnswerContainsCriterion
    | AnswerExcludesCriterion
    | AnswerMatchesRegexCriterion
    | DeclaredOutcomeCriterion
    | ToolCallCountCriterion
    | CitationsWithinCriterion
    | AnswerExactCriterion
    | RequiredFactsCriterion
    | ForbiddenClaimsCriterion
    | StructuredOutputCriterion
    | ToolCallsRequiredCriterion
    | ProhibitedActionsCriterion
    | ExecutionLimitsCriterion
    | ExpectedRecoveryCriterion
    | RubricCriterion,
    Field(discriminator="type"),
]


# --- Expectations --------------------------------------------------------------------------


class ExpectedOutcome(DomainModel):
    """The behaviour a correct agent exhibits."""

    outcome: AgentOutcome
    description: NonEmptyStr
    reference_answer: NonEmptyStr | None = Field(
        default=None, description="Canonical answer; only meaningful when outcome is 'answered'."
    )

    @model_validator(mode="after")
    def _reference_only_for_answers(self) -> Self:
        if self.reference_answer is not None and self.outcome is not AgentOutcome.ANSWERED:
            raise ValueError("reference_answer is only allowed when outcome is 'answered'")
        return self


class ScoringPolicy(DomainModel):
    """Score = sum(weight of passed criteria) / sum(all weights). A run passes when every
    required criterion passes and the score is at least ``pass_threshold``."""

    pass_threshold: float = Field(default=1.0, gt=0.0, le=1.0)


class Expectation(DomainModel):
    expected_behavior: ExpectedOutcome
    evaluation_criteria: list[EvaluationCriterion] = Field(min_length=1)
    scoring: ScoringPolicy = Field(default_factory=ScoringPolicy)

    @model_validator(mode="after")
    def _criteria_consistent(self) -> Self:
        require_unique((c.id for c in self.evaluation_criteria), "criterion ids")
        for criterion in self.evaluation_criteria:
            if (
                isinstance(criterion, DeclaredOutcomeCriterion)
                and criterion.expected is not self.expected_behavior.outcome
            ):
                raise ValueError(
                    f"criterion '{criterion.id}' expects outcome '{criterion.expected}' but "
                    f"expected_behavior.outcome is '{self.expected_behavior.outcome}'"
                )
        return self


# --- Cases ---------------------------------------------------------------------------------


class SourceDocument(DomainModel):
    """A document provided to the agent as part of the task."""

    id: Slug
    title: NonEmptyStr
    content: NonEmptyStr


class TaskInputs(DomainModel):
    prompt: NonEmptyStr = Field(description="The instruction given to the agent.")
    documents: list[SourceDocument] = Field(default_factory=list)
    available_tools: list[Slug] = Field(default_factory=list)
    variables: dict[Slug, ScalarValue] = Field(
        default_factory=dict, description="Named scalar parameters referenced by the prompt."
    )

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        require_unique((d.id for d in self.documents), "document ids")
        require_unique(self.available_tools, "available_tools")
        return self


class CaseMetadata(DomainModel):
    synthetic: bool = Field(description="True if the case content was invented for testing.")
    provenance: NonEmptyStr = Field(description="Where the case content came from.")
    notes: NonEmptyStr | None = None


class BenchmarkCase(DomainModel):
    id: Slug
    title: NonEmptyStr
    task_description: NonEmptyStr = Field(description="What the case tests, for humans.")
    inputs: TaskInputs
    expectation: Expectation
    tags: list[Slug] = Field(default_factory=list)
    difficulty: Difficulty
    metadata: CaseMetadata

    @model_validator(mode="after")
    def _references_valid(self) -> Self:
        require_unique(self.tags, "tags")
        check_expectation_references(self.expectation, self.inputs)
        if any(
            isinstance(c, ExpectedRecoveryCriterion) for c in self.expectation.evaluation_criteria
        ):
            raise ValueError(
                "expected_recovery criteria refer to scenario faults and are only allowed in a "
                "fault-injected scenario's expectation_override"
            )
        return self


def check_expectation_references(expectation: Expectation, inputs: TaskInputs) -> None:
    """Ensure criteria only reference tools and documents that the case provides."""
    tools = set(inputs.available_tools)
    documents = {d.id for d in inputs.documents}
    for criterion in expectation.evaluation_criteria:
        if isinstance(criterion, ToolCallCountCriterion) and criterion.tool_name not in tools:
            raise ValueError(
                f"criterion '{criterion.id}' references unknown tool '{criterion.tool_name}'"
            )
        if isinstance(criterion, ToolCallsRequiredCriterion):
            unknown_tools = sorted({c.tool_name for c in criterion.calls} - tools)
            if unknown_tools:
                raise ValueError(
                    f"criterion '{criterion.id}' references unknown tools: "
                    f"{', '.join(unknown_tools)}"
                )
        if isinstance(criterion, CitationsWithinCriterion):
            unknown = sorted(set(criterion.allowed_source_ids) - documents)
            if unknown:
                raise ValueError(
                    f"criterion '{criterion.id}' references unknown documents: {', '.join(unknown)}"
                )


# --- Scenarios -----------------------------------------------------------------------------


class Scenario(DomainModel):
    id: Slug
    case_id: Slug
    kind: ScenarioKind
    description: NonEmptyStr
    faults: list[FaultConfig] = Field(default_factory=list)
    expectation_override: Expectation | None = Field(
        default=None,
        description="Replaces the case expectation for this scenario (fault-injected only).",
    )

    @model_validator(mode="after")
    def _kind_consistent(self) -> Self:
        if self.kind is ScenarioKind.BASELINE:
            if self.faults:
                raise ValueError("baseline scenarios must not inject faults")
            if self.expectation_override is not None:
                raise ValueError("baseline scenarios must use the case expectation")
        elif not self.faults:
            raise ValueError("fault_injected scenarios must define at least one fault")
        require_unique((f.id for f in self.faults), "fault ids")
        if self.expectation_override is not None:
            fault_ids = {f.id for f in self.faults}
            for criterion in self.expectation_override.evaluation_criteria:
                if isinstance(criterion, ExpectedRecoveryCriterion):
                    unknown = sorted(set(criterion.fault_ids) - fault_ids)
                    if unknown:
                        raise ValueError(
                            f"criterion '{criterion.id}' references faults not in scenario "
                            f"'{self.id}': {', '.join(unknown)}"
                        )
        return self


# --- Suite ---------------------------------------------------------------------------------


class BenchmarkSuite(DomainModel):
    """A versioned collection of cases and scenarios; the root of a dataset file."""

    schema_version: Literal["1.0"] = Field(description="Dataset file format version.")
    id: Slug
    version: SemVer = Field(description="Content version of this suite.")
    name: NonEmptyStr
    description: NonEmptyStr
    synthetic: bool = Field(description="True if any case in the suite is synthetic.")
    created_at: UtcDateTime
    cases: list[BenchmarkCase] = Field(min_length=1)
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def _relationships_valid(self) -> Self:
        require_unique((c.id for c in self.cases), "case ids")
        require_unique((s.id for s in self.scenarios), "scenario ids")

        if any(c.metadata.synthetic for c in self.cases) and not self.synthetic:
            raise ValueError("suite contains synthetic cases and must set synthetic: true")

        cases = {c.id: c for c in self.cases}
        baselines: Counter[str] = Counter()
        for scenario in self.scenarios:
            case = cases.get(scenario.case_id)
            if case is None:
                raise ValueError(
                    f"scenario '{scenario.id}' references unknown case '{scenario.case_id}'"
                )
            if scenario.kind is ScenarioKind.BASELINE:
                baselines[case.id] += 1
            for fault in scenario.faults:
                if fault.target_tool not in case.inputs.available_tools:
                    raise ValueError(
                        f"fault '{fault.id}' in scenario '{scenario.id}' targets tool "
                        f"'{fault.target_tool}' which case '{case.id}' does not provide"
                    )
            if scenario.expectation_override is not None:
                check_expectation_references(scenario.expectation_override, case.inputs)

        for case_id in cases:
            if baselines[case_id] != 1:
                raise ValueError(
                    f"case '{case_id}' must have exactly one baseline scenario, "
                    f"found {baselines[case_id]}"
                )
        return self

    def get_case(self, case_id: str) -> BenchmarkCase:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)

    def get_scenario(self, scenario_id: str) -> Scenario:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(scenario_id)

    def scenarios_for(self, case_id: str) -> list[Scenario]:
        return [s for s in self.scenarios if s.case_id == case_id]

    def effective_expectation(self, scenario_id: str) -> Expectation:
        """The expectation a run of this scenario is scored against."""
        scenario = self.get_scenario(scenario_id)
        if scenario.expectation_override is not None:
            return scenario.expectation_override
        return self.get_case(scenario.case_id).expectation
