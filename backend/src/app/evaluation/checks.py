"""Deterministic criterion checks.

Each check reads only the ``RunView`` (the stored trace) and the benchmark definitions, and
returns a ``CriterionEvaluation`` with separate status, evidence, and explanation. Rules shared
by all answer checks:

* No final answer (run did not complete): presence checks fail; absence checks pass
  vacuously (nothing was asserted). The implicit ``run-completed`` criterion carries the
  actual failure.
* Truncated stored answer: a *found* match is decisive; a *missing* match is inconclusive,
  because the text may be in the part that was cut.
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from app.domain.benchmark import (
    CITATION_PATTERN,
    AnswerContainsCriterion,
    AnswerExactCriterion,
    AnswerExcludesCriterion,
    AnswerMatchesRegexCriterion,
    BenchmarkCase,
    CitationsWithinCriterion,
    DeclaredOutcomeCriterion,
    ExecutionLimitsCriterion,
    ExpectedRecoveryCriterion,
    ForbiddenClaimsCriterion,
    ProhibitedActionsCriterion,
    RequiredFactsCriterion,
    StructuredOutputCriterion,
    TextMatcher,
    ToolCallCountCriterion,
    ToolCallsRequiredCriterion,
)
from app.domain.enums import (
    AgentOutcome,
    AnswerFormat,
    CriterionType,
    MatchMode,
    Normalization,
    RecoveryBehavior,
    RunStatus,
)
from app.evaluation.models import (
    CriterionEvaluation,
    CriterionStatus,
    EvaluationLayer,
    EvaluatorKind,
    Evidence,
    EvidenceKind,
    FailureLabel,
)
from app.evaluation.run_view import FinalAnswerView, RunView, ToolCallView

EXCERPT_CHARS = 160
IMPLICIT_RUN_COMPLETED = "implicit.run-completed"

LAYER_BY_TYPE: dict[CriterionType, EvaluationLayer] = {
    CriterionType.DECLARED_OUTCOME: EvaluationLayer.ANSWER,
    CriterionType.ANSWER_CONTAINS: EvaluationLayer.ANSWER,
    CriterionType.ANSWER_EXCLUDES: EvaluationLayer.ANSWER,
    CriterionType.ANSWER_MATCHES_REGEX: EvaluationLayer.ANSWER,
    CriterionType.ANSWER_EXACT: EvaluationLayer.ANSWER,
    CriterionType.REQUIRED_FACTS: EvaluationLayer.ANSWER,
    CriterionType.FORBIDDEN_CLAIMS: EvaluationLayer.ANSWER,
    CriterionType.CITATIONS_WITHIN: EvaluationLayer.ANSWER,
    CriterionType.STRUCTURED_OUTPUT: EvaluationLayer.ANSWER,
    CriterionType.RUBRIC: EvaluationLayer.ANSWER,
    CriterionType.TOOL_CALL_COUNT: EvaluationLayer.TOOL_BEHAVIOR,
    CriterionType.TOOL_CALLS_REQUIRED: EvaluationLayer.TOOL_BEHAVIOR,
    CriterionType.PROHIBITED_ACTIONS: EvaluationLayer.TOOL_BEHAVIOR,
    CriterionType.EXPECTED_RECOVERY: EvaluationLayer.TOOL_BEHAVIOR,
    CriterionType.EXECUTION_LIMITS: EvaluationLayer.EXECUTION,
}


@dataclass(frozen=True)
class CheckContext:
    view: RunView
    case: BenchmarkCase


@dataclass(frozen=True)
class Outcome:
    """What a check decided, before it is wrapped into a ``CriterionEvaluation``."""

    status: CriterionStatus
    explanation: str
    evidence: list[Evidence]
    labels: list[FailureLabel]


def passed(explanation: str, evidence: list[Evidence]) -> Outcome:
    return Outcome(CriterionStatus.PASSED, explanation, evidence, [])


def failed(explanation: str, evidence: list[Evidence], *labels: FailureLabel) -> Outcome:
    return Outcome(CriterionStatus.FAILED, explanation, evidence, list(labels))


def inconclusive(explanation: str, evidence: list[Evidence]) -> Outcome:
    return Outcome(
        CriterionStatus.INCONCLUSIVE, explanation, evidence, [FailureLabel.EVALUATOR_UNCERTAINTY]
    )


def excerpt(text: str, start: int = 0, end: int | None = None) -> str:
    """Up to ``EXCERPT_CHARS`` characters around ``text[start:end]``."""
    end = len(text) if end is None else end
    margin = max(0, (EXCERPT_CHARS - (end - start)) // 2)
    lo, hi = max(0, start - margin), min(len(text), end + margin)
    snippet = text[lo:hi][:EXCERPT_CHARS]
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


# --- shared answer helpers -------------------------------------------------------------------


def _no_answer_evidence(view: RunView) -> list[Evidence]:
    event_ids = [view.error_event.id] if view.error_event else [view.root_event_id]
    return [
        Evidence(
            kind=EvidenceKind.RUN,
            statement=f"The run ended with status '{view.run_status}' and produced no final "
            "answer.",
            event_ids=event_ids,
        )
    ]


def _answer_evidence(
    answer: FinalAnswerView, statement: str, start: int = 0, end: int | None = None
) -> Evidence:
    return Evidence(
        kind=EvidenceKind.ANSWER,
        statement=statement,
        event_ids=[answer.event_id],
        excerpt=excerpt(answer.content, start, end),
    )


def _truncation_evidence(answer: FinalAnswerView) -> Evidence:
    return Evidence(
        kind=EvidenceKind.ANSWER,
        statement="The stored final answer is truncated; text beyond the cut is unavailable.",
        event_ids=[answer.event_id],
    )


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    matched: str


def _find_literal(text: str, value: str, case_sensitive: bool) -> _Match | None:
    haystack, needle = (text, value) if case_sensitive else (text.casefold(), value.casefold())
    index = haystack.find(needle)
    return (
        None if index < 0 else _Match(index, index + len(value), text[index : index + len(value)])
    )


def _find_matcher(text: str, matcher: TextMatcher) -> _Match | None:
    for phrase in matcher.phrases:
        found = _find_literal(text, phrase, case_sensitive=False)
        if found:
            return found
    for pattern in matcher.patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _Match(match.start(), match.end(), match.group(0))
    return None


def _provenance(view: RunView, matcher: TextMatcher | str) -> Evidence:
    """Was this expected text present in what the agent received from its tools?"""

    def find(text: str) -> _Match | None:
        if isinstance(matcher, str):
            return _find_literal(text, matcher, case_sensitive=False)
        return _find_matcher(text, matcher)

    label = f"'{matcher}'" if isinstance(matcher, str) else f"fact '{matcher.id}'"
    hits = [(c, m) for c in view.tool_calls if c.succeeded and (m := find(c.output_text))]
    if hits:
        first, match = hits[0]
        return Evidence(
            kind=EvidenceKind.CONTEXT_AVAILABLE,
            statement=f"{label} appears in {len(hits)} successful tool result(s) the agent "
            "received.",
            event_ids=[c.result_event_id for c, _ in hits],
            excerpt=excerpt(first.output_text, match.start, match.end),
        )
    truncated = [c.result_event_id for c in view.tool_calls if c.output_truncated]
    return Evidence(
        kind=EvidenceKind.CONTEXT_ABSENT,
        statement=f"{label} appears in no successful tool result"
        + (" (some stored tool results are truncated)." if truncated else "."),
        event_ids=truncated,
    )


def _missing_labels(evidence: Sequence[Evidence]) -> list[FailureLabel]:
    labels = [FailureLabel.TASK_CORRECTNESS]
    if any(e.kind is EvidenceKind.CONTEXT_AVAILABLE for e in evidence):
        labels.append(FailureLabel.CONTEXT_RETENTION)
    return labels


# --- answer layer ----------------------------------------------------------------------------


def check_declared_outcome(criterion: DeclaredOutcomeCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return failed(
            f"Expected outcome '{criterion.expected}', but there is no final answer.",
            _no_answer_evidence(ctx.view),
            FailureLabel.TASK_CORRECTNESS,
        )
    evidence = [
        Evidence(
            kind=EvidenceKind.ANSWER,
            statement=f"The agent declared outcome '{answer.declared_outcome}'.",
            event_ids=[answer.event_id],
        )
    ]
    if answer.declared_outcome is criterion.expected:
        return passed(f"Declared outcome is '{criterion.expected}' as expected.", evidence)
    return failed(
        f"Expected outcome '{criterion.expected}', agent declared '{answer.declared_outcome}'.",
        evidence,
        FailureLabel.TASK_CORRECTNESS,
    )


def check_answer_contains(criterion: AnswerContainsCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return failed(
            "No final answer to search.",
            _no_answer_evidence(ctx.view),
            FailureLabel.TASK_CORRECTNESS,
        )
    found: list[tuple[str, _Match]] = []
    missing: list[str] = []
    for value in criterion.values:
        match = _find_literal(answer.content, value, criterion.case_sensitive)
        if match:
            found.append((value, match))
        else:
            missing.append(value)
    evidence = [
        _answer_evidence(answer, f"Answer contains '{v}'.", m.start, m.end) for v, m in found
    ]
    ok = not missing if criterion.match is MatchMode.ALL else bool(found)
    if ok:
        return passed(f"Answer contains the required value(s) (match={criterion.match}).", evidence)
    evidence += [_provenance(ctx.view, value) for value in missing]
    if answer.truncated:
        return inconclusive(
            f"Not found in the stored answer: {missing}; the answer is truncated.",
            [*evidence, _truncation_evidence(answer)],
        )
    return failed(
        f"Answer is missing {missing} (match={criterion.match}).",
        evidence,
        *_missing_labels(evidence),
    )


def check_answer_excludes(criterion: AnswerExcludesCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return passed(
            "No final answer, so nothing excluded was asserted.", _no_answer_evidence(ctx.view)
        )
    evidence: list[Evidence] = []
    for value in criterion.values:
        match = _find_literal(answer.content, value, criterion.case_sensitive)
        if match:
            evidence.append(
                _answer_evidence(
                    answer, f"Answer contains excluded text '{value}'.", match.start, match.end
                )
            )
            source = _provenance(ctx.view, value)
            if source.kind is EvidenceKind.CONTEXT_AVAILABLE:
                evidence.append(source)
    if evidence:
        return failed("Answer contains excluded text.", evidence, FailureLabel.TASK_CORRECTNESS)
    if answer.truncated:
        return inconclusive(
            "No excluded text in the stored answer, but it is truncated.",
            [_truncation_evidence(answer)],
        )
    return passed(f"Answer contains none of {criterion.values}.", [])


def check_answer_matches_regex(
    criterion: AnswerMatchesRegexCriterion, ctx: CheckContext
) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return failed(
            "No final answer to match.",
            _no_answer_evidence(ctx.view),
            FailureLabel.TASK_CORRECTNESS,
        )
    match = re.search(criterion.pattern, answer.content)
    if match:
        return passed(
            f"Answer matches /{criterion.pattern}/.",
            [_answer_evidence(answer, f"Match: '{match.group(0)}'.", match.start(), match.end())],
        )
    if answer.truncated:
        return inconclusive(
            f"No match for /{criterion.pattern}/ in the truncated answer.",
            [_truncation_evidence(answer)],
        )
    return failed(
        f"Answer does not match /{criterion.pattern}/.",
        [_answer_evidence(answer, "The final answer (no match).")],
        FailureLabel.TASK_CORRECTNESS,
    )


_PUNCTUATION_EDGES = re.compile(r"^[\s\W_]+|[\s\W_]+$")


def normalize_answer(text: str, mode: Normalization) -> str:
    if mode is Normalization.NONE:
        return text
    text = re.sub(CITATION_PATTERN, " ", text)
    text = " ".join(text.casefold().split())
    return _PUNCTUATION_EDGES.sub("", text)


def resolve_pointer(document: JsonValue, pointer: str) -> JsonValue:
    """RFC 6901. Raises ``KeyError`` if the path does not exist."""
    current = document
    if pointer == "":
        return current
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


def check_answer_exact(criterion: AnswerExactCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return failed(
            "No final answer to compare.",
            _no_answer_evidence(ctx.view),
            FailureLabel.TASK_CORRECTNESS,
        )
    if answer.truncated:
        return inconclusive(
            "The stored answer is truncated; exact comparison is impossible.",
            [_truncation_evidence(answer)],
        )
    labels = [FailureLabel.TASK_CORRECTNESS]
    candidate: str
    if criterion.json_pointer is not None:
        try:
            value = resolve_pointer(json.loads(answer.content), criterion.json_pointer)
        except json.JSONDecodeError as exc:
            return failed(
                f"Answer is not JSON ({exc.msg}), so '{criterion.json_pointer}' cannot be read.",
                [_answer_evidence(answer, "The final answer.")],
                *labels,
                FailureLabel.INVALID_OUTPUT,
            )
        except KeyError:
            return failed(
                f"JSON answer has no value at '{criterion.json_pointer}'.",
                [_answer_evidence(answer, "The final answer.")],
                *labels,
                FailureLabel.INVALID_OUTPUT,
            )
        candidate = value if isinstance(value, str) else json.dumps(value)
    else:
        candidate = answer.content
    normalized = normalize_answer(candidate, criterion.normalization)
    accepted = {normalize_answer(a, criterion.normalization): a for a in criterion.accepted}
    evidence = [
        Evidence(
            kind=EvidenceKind.ANSWER,
            statement=f"Compared value after '{criterion.normalization}' normalisation.",
            event_ids=[answer.event_id],
            excerpt=excerpt(normalized),
        )
    ]
    if normalized in accepted:
        return passed(f"Answer equals accepted value '{accepted[normalized]}'.", evidence)
    evidence.append(_provenance(ctx.view, criterion.accepted[0]))
    return failed(
        f"Answer does not equal any of {criterion.accepted}.", evidence, *_missing_labels(evidence)
    )


def check_required_facts(criterion: RequiredFactsCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return failed(
            "No final answer to check.",
            _no_answer_evidence(ctx.view),
            FailureLabel.TASK_CORRECTNESS,
        )
    needed = criterion.min_present or len(criterion.facts)
    evidence: list[Evidence] = []
    present, missing = [], []
    for fact in criterion.facts:
        match = _find_matcher(answer.content, fact)
        if match:
            present.append(fact.id)
            evidence.append(
                _answer_evidence(answer, f"Fact '{fact.id}' stated.", match.start, match.end)
            )
        else:
            missing.append(fact.id)
            evidence.append(_provenance(ctx.view, fact))
    summary = (
        f"{len(present)}/{len(criterion.facts)} facts present (need {needed}); "
        f"missing: {missing or 'none'}."
    )
    if len(present) >= needed:
        return passed(summary, evidence)
    if answer.truncated:
        return inconclusive(
            summary + " The stored answer is truncated.", [*evidence, _truncation_evidence(answer)]
        )
    return failed(summary, evidence, *_missing_labels(evidence))


def check_forbidden_claims(criterion: ForbiddenClaimsCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        return passed("No final answer, so no claim was asserted.", _no_answer_evidence(ctx.view))
    evidence: list[Evidence] = []
    labels = [FailureLabel.TASK_CORRECTNESS]
    hits = []
    for claim in criterion.claims:
        match = _find_matcher(answer.content, claim)
        if match is None:
            continue
        hits.append(claim.id)
        evidence.append(
            _answer_evidence(
                answer,
                f"Forbidden claim '{claim.id}' matched '{match.matched}'.",
                match.start,
                match.end,
            )
        )
        source = _provenance(ctx.view, match.matched)
        if source.kind is EvidenceKind.CONTEXT_AVAILABLE:
            evidence.append(source)
        if claim.security_sensitive and FailureLabel.SECURITY_VIOLATION not in labels:
            labels.append(FailureLabel.SECURITY_VIOLATION)
    if hits:
        return failed(
            f"Answer matches forbidden claim(s) {hits}. Text matching cannot tell an assertion "
            "from a quotation; check the excerpt.",
            evidence,
            *labels,
        )
    if answer.truncated:
        return inconclusive(
            "No forbidden claim in the stored answer, but it is truncated.",
            [_truncation_evidence(answer)],
        )
    return passed("Answer matches none of the forbidden claims.", [])


def check_citations(criterion: CitationsWithinCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    if answer is None:
        if criterion.min_citations == 0:
            return passed(
                "No final answer and no citations required.", _no_answer_evidence(ctx.view)
            )
        return failed(
            "No final answer, so no citations.",
            _no_answer_evidence(ctx.view),
            FailureLabel.EVIDENCE_HANDLING,
        )
    documents = {d.id for d in ctx.case.inputs.documents}
    allowed = set(criterion.allowed_source_ids)
    cited = answer.cited_ids
    evidence: list[Evidence] = []
    problems: list[str] = []
    for source_id in cited:
        marker = f"[source:{source_id}]"
        start = answer.content.find(marker)
        if source_id not in documents:
            problems.append(f"'{source_id}' is not a document of this case (fabricated)")
            evidence.append(
                _answer_evidence(
                    answer, f"Cites unknown source '{source_id}'.", start, start + len(marker)
                )
            )
        elif source_id not in allowed:
            problems.append(f"'{source_id}' is not an allowed source")
            evidence.append(
                _answer_evidence(
                    answer, f"Cites disallowed source '{source_id}'.", start, start + len(marker)
                )
            )
        provenance = _provenance(ctx.view, source_id)
        if provenance.kind is EvidenceKind.CONTEXT_ABSENT:
            evidence.append(
                Evidence(
                    kind=EvidenceKind.CITATION,
                    statement=f"Cited '{source_id}' never appeared in a successful tool result.",
                    event_ids=provenance.event_ids,
                )
            )
            if criterion.require_tool_provenance:
                problems.append(f"'{source_id}' was never retrieved")
    declared_only = sorted(set(answer.source_ids) - set(cited))
    if declared_only:
        evidence.append(
            Evidence(
                kind=EvidenceKind.CITATION,
                statement=f"Declared source_ids not cited inline: {declared_only}.",
                event_ids=[answer.event_id],
            )
        )
    summary = (
        f"Cited {len(cited)} distinct source(s): {cited or 'none'}; need {criterion.min_citations}."
    )
    if problems:
        return failed(
            summary + " Problems: " + "; ".join(problems) + ".",
            evidence,
            FailureLabel.EVIDENCE_HANDLING,
        )
    if len(cited) < criterion.min_citations:
        if answer.truncated:
            return inconclusive(
                summary + " The stored answer is truncated.",
                [*evidence, _truncation_evidence(answer)],
            )
        return failed(summary, evidence, FailureLabel.EVIDENCE_HANDLING)
    return passed(summary, evidence)


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n?```", re.DOTALL)


def check_structured_output(criterion: StructuredOutputCriterion, ctx: CheckContext) -> Outcome:
    answer = ctx.view.answer
    labels = (FailureLabel.INVALID_OUTPUT,)
    if answer is None:
        return failed("No final answer to validate.", _no_answer_evidence(ctx.view), *labels)
    if answer.truncated:
        return inconclusive(
            "The stored answer is truncated; JSON cannot be validated.",
            [_truncation_evidence(answer)],
        )
    if criterion.answer_format is AnswerFormat.JSON_BLOCK:
        blocks = _JSON_BLOCK.findall(answer.content)
        if len(blocks) != 1:
            return failed(
                f"Expected exactly one ```json block, found {len(blocks)}.",
                [_answer_evidence(answer, "The final answer.")],
                *labels,
            )
        raw = blocks[0]
    else:
        raw = answer.content
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        return failed(
            f"Answer is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}.",
            [_answer_evidence(answer, "The final answer.")],
            *labels,
        )
    errors = sorted(
        Draft202012Validator(criterion.json_schema).iter_errors(document),
        key=lambda e: list(e.path),
    )
    if errors:
        evidence = [
            Evidence(
                kind=EvidenceKind.SCHEMA,
                statement=f"At '/{'/'.join(str(p) for p in error.path)}': {error.message}",
                event_ids=[answer.event_id],
            )
            for error in errors[:10]
        ]
        return failed(
            f"JSON answer violates the schema ({len(errors)} error(s)).", evidence, *labels
        )
    return passed("Answer is JSON valid against the schema.", [])


# --- tool behaviour layer ----------------------------------------------------------------------


def _call_evidence(call: ToolCallView, statement: str) -> Evidence:
    return Evidence(
        kind=EvidenceKind.TOOL_CALL,
        statement=statement,
        event_ids=[call.event_id, call.result_event_id],
        excerpt=excerpt(json.dumps(call.arguments, sort_keys=True)),
    )


def check_tool_call_count(criterion: ToolCallCountCriterion, ctx: CheckContext) -> Outcome:
    calls = [c for c in ctx.view.tool_calls if c.tool_name == criterion.tool_name]
    upper = "∞" if criterion.max_calls is None else str(criterion.max_calls)
    bounds = f"[{criterion.min_calls}, {upper}]"
    evidence = [
        Evidence(
            kind=EvidenceKind.TOOL_CALL,
            statement=f"'{criterion.tool_name}' was called {len(calls)} time(s).",
            event_ids=[c.event_id for c in calls],
        )
    ]
    if len(calls) < criterion.min_calls:
        return failed(
            f"{len(calls)} call(s) is below {bounds}.", evidence, FailureLabel.TOOL_FAILURE
        )
    if criterion.max_calls is not None and len(calls) > criterion.max_calls:
        return failed(
            f"{len(calls)} call(s) is above {bounds}.",
            evidence,
            FailureLabel.EXECUTION_LIMIT_VIOLATION,
        )
    return passed(f"{len(calls)} call(s) within {bounds}.", evidence)


def check_tool_calls_required(criterion: ToolCallsRequiredCriterion, ctx: CheckContext) -> Outcome:
    evidence: list[Evidence] = []
    unmet: list[str] = []
    for spec in criterion.calls:
        matching = [
            c
            for c in ctx.view.tool_calls
            if c.tool_name == spec.tool_name
            and all(c.arguments.get(k) == v for k, v in spec.arguments.items())
            and (c.succeeded or not spec.must_succeed)
        ]
        description = spec.tool_name + (f" {spec.arguments}" if spec.arguments else "")
        if spec.must_succeed:
            description += " (successful)"
        if len(matching) >= spec.min_count:
            evidence.append(
                Evidence(
                    kind=EvidenceKind.TOOL_CALL,
                    statement=f"{len(matching)} matching call(s) to {description}.",
                    event_ids=[c.event_id for c in matching],
                )
            )
        else:
            unmet.append(f"{description}: {len(matching)}/{spec.min_count}")
    if unmet:
        return failed(
            "Required calls missing: " + "; ".join(unmet) + ".", evidence, FailureLabel.TOOL_FAILURE
        )
    return passed("All required tool calls were made.", evidence)


def check_prohibited_actions(criterion: ProhibitedActionsCriterion, ctx: CheckContext) -> Outcome:
    prohibited = set(criterion.tool_names)
    available = set(ctx.case.inputs.available_tools)
    evidence: list[Evidence] = []
    for call in ctx.view.tool_calls:
        if call.tool_name in prohibited:
            evidence.append(_call_evidence(call, f"Called prohibited tool '{call.tool_name}'."))
        elif criterion.forbid_unavailable_tools and call.tool_name not in available:
            evidence.append(_call_evidence(call, f"Called unavailable tool '{call.tool_name}'."))
    for request in ctx.view.rejected_requests:
        if request.tool_name in prohibited or criterion.forbid_unavailable_tools:
            reason = "prohibited" if request.tool_name in prohibited else "unavailable"
            evidence.append(
                Evidence(
                    kind=EvidenceKind.TOOL_CALL,
                    statement=f"Attempted to call {reason} tool '{request.tool_name}' "
                    f"(rejected: {request.error_type}).",
                    event_ids=[request.event_id],
                )
            )
    if evidence:
        return failed(
            f"{len(evidence)} unauthorized action(s) or attempt(s). An attempt counts even when "
            "the harness rejected it.",
            evidence,
            FailureLabel.SECURITY_VIOLATION,
        )
    return passed("No prohibited or unavailable tool was called or requested.", [])


def _faulted_calls(view: RunView, fault_id: str) -> list[tuple[str, ToolCallView]]:
    return [(fault.event_id, call) for fault, call in view.faults if fault.fault_id == fault_id]


def check_expected_recovery(criterion: ExpectedRecoveryCriterion, ctx: CheckContext) -> Outcome:
    view = ctx.view
    evidence: list[Evidence] = []
    problems: list[str] = []
    for fault_id in criterion.fault_ids:
        activations = _faulted_calls(view, fault_id)
        if not activations:
            return inconclusive(
                f"Fault '{fault_id}' never fired in this run, so the expected failure did not "
                "occur and recovery cannot be assessed.",
                evidence,
            )
        for fault_event_id, call in activations:
            evidence.append(
                Evidence(
                    kind=EvidenceKind.FAULT,
                    statement=f"Fault '{fault_id}' fired on '{call.tool_name}' "
                    f"(call status '{call.status}').",
                    event_ids=[fault_event_id, call.event_id, call.result_event_id],
                )
            )
            if criterion.max_attempts is not None:
                identical = [
                    c
                    for c in view.tool_calls
                    if c.tool_name == call.tool_name
                    and c.arguments == call.arguments
                    and c.sequence >= call.sequence
                ]
                if len(identical) > criterion.max_attempts:
                    problems.append(
                        f"{len(identical)} identical '{call.tool_name}' attempts from the faulted "
                        f"call on (max {criterion.max_attempts})"
                    )
                    evidence.append(
                        Evidence(
                            kind=EvidenceKind.TOOL_CALL,
                            statement=f"{len(identical)} identical attempts.",
                            event_ids=[c.event_id for c in identical],
                        )
                    )
            if criterion.behavior is RecoveryBehavior.RETRY_SUCCEEDED:
                later = [
                    c
                    for c in view.tool_calls
                    if c.tool_name == call.tool_name and c.sequence > call.sequence and c.succeeded
                ]
                if later:
                    evidence.append(
                        _call_evidence(later[0], f"Later successful '{call.tool_name}' call.")
                    )
                else:
                    problems.append(
                        f"no successful '{call.tool_name}' call after fault '{fault_id}'"
                    )
    if criterion.behavior is RecoveryBehavior.FAILURE_REPORTED:
        answer = view.answer
        if answer is None:
            problems.append("no final answer, so the failure was not reported")
        elif answer.declared_outcome is AgentOutcome.ANSWERED:
            problems.append("the agent declared 'answered' instead of reporting the failure")
            evidence.append(_answer_evidence(answer, "Declared 'answered'."))
        else:
            evidence.append(_answer_evidence(answer, f"Declared '{answer.declared_outcome}'."))
    if problems:
        return failed(
            "Expected recovery did not happen: " + "; ".join(problems) + ".",
            evidence,
            FailureLabel.TOOL_FAILURE,
        )
    return passed(
        f"Faults fired and the agent behaved as expected ({criterion.behavior}).", evidence
    )


# --- execution layer ---------------------------------------------------------------------------


def _run_status_outcome(view: RunView) -> Outcome | None:
    if view.run_status is RunStatus.COMPLETED:
        return None
    label = (
        FailureLabel.RUN_ERROR
        if view.run_status is RunStatus.ERRORED
        else FailureLabel.EXECUTION_LIMIT_VIOLATION
    )
    detail = view.error_event.error_type if view.error_event else view.termination_reason
    return failed(
        f"The run did not complete: status '{view.run_status}' ({detail}).",
        _no_answer_evidence(view),
        label,
    )


def check_run_completed(ctx: CheckContext) -> Outcome:
    return _run_status_outcome(ctx.view) or passed(
        "The run completed without hitting a harness limit.",
        [
            Evidence(
                kind=EvidenceKind.RUN,
                statement="Run status 'completed'.",
                event_ids=[ctx.view.root_event_id],
            )
        ],
    )


def check_execution_limits(criterion: ExecutionLimitsCriterion, ctx: CheckContext) -> Outcome:
    status = _run_status_outcome(ctx.view)
    if status is not None:
        return status
    view = ctx.view
    failed_calls = [c for c in view.tool_calls if not c.succeeded]
    measured = [
        ("tool calls", len(view.tool_calls), criterion.max_tool_calls),
        ("failed tool calls", len(failed_calls), criterion.max_failed_tool_calls),
        ("steps", view.step_count, criterion.max_steps),
        ("duration (s)", view.duration_seconds, criterion.max_duration_seconds),
    ]
    over = [
        f"{name} {value:g} > {limit:g}"
        for name, value, limit in measured
        if limit is not None and value > limit
    ]
    evidence = [
        Evidence(
            kind=EvidenceKind.RUN,
            statement="; ".join(f"{name}: {value:g}" for name, value, _ in measured) + ".",
            event_ids=[view.root_event_id],
        )
    ]
    if over:
        return failed(
            "Budget exceeded: " + "; ".join(over) + ".",
            evidence,
            FailureLabel.EXECUTION_LIMIT_VIOLATION,
        )
    return passed("Completed within the configured budgets.", evidence)


Check = Callable[..., Outcome]

CHECKS: dict[CriterionType, Check] = {
    CriterionType.DECLARED_OUTCOME: check_declared_outcome,
    CriterionType.ANSWER_CONTAINS: check_answer_contains,
    CriterionType.ANSWER_EXCLUDES: check_answer_excludes,
    CriterionType.ANSWER_MATCHES_REGEX: check_answer_matches_regex,
    CriterionType.ANSWER_EXACT: check_answer_exact,
    CriterionType.REQUIRED_FACTS: check_required_facts,
    CriterionType.FORBIDDEN_CLAIMS: check_forbidden_claims,
    CriterionType.CITATIONS_WITHIN: check_citations,
    CriterionType.STRUCTURED_OUTPUT: check_structured_output,
    CriterionType.TOOL_CALL_COUNT: check_tool_call_count,
    CriterionType.TOOL_CALLS_REQUIRED: check_tool_calls_required,
    CriterionType.PROHIBITED_ACTIONS: check_prohibited_actions,
    CriterionType.EXPECTED_RECOVERY: check_expected_recovery,
    CriterionType.EXECUTION_LIMITS: check_execution_limits,
}
"""Every criterion type except ``rubric``, which needs a judge."""


def to_evaluation(
    *,
    criterion_id: str,
    criterion_type: CriterionType,
    weight: float,
    required: bool,
    outcome: Outcome,
    implicit: bool = False,
) -> CriterionEvaluation:
    return CriterionEvaluation(
        criterion_id=criterion_id,
        criterion_type=criterion_type,
        layer=LAYER_BY_TYPE[criterion_type],
        evaluator=EvaluatorKind.DETERMINISTIC,
        implicit=implicit,
        status=outcome.status,
        weight=weight,
        required=required,
        score=None
        if outcome.status is CriterionStatus.INCONCLUSIVE
        else float(outcome.status is CriterionStatus.PASSED),
        evidence=outcome.evidence,
        explanation=outcome.explanation,
        labels=outcome.labels,
    )
