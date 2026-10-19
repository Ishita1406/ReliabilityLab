"""Deterministic research agent (no LLM).

Decision procedure:

1. Search with ``task.variables["search_query"]``.
2. Fetch up to ``max_sources`` of the top results.
3. Extract a value from each document with ``task.variables["answer_pattern"]`` (a regex with
   exactly one capture group), recording the sentence it came from.
4. Compare the extracted values across sources:
   * one distinct value -> ``answered``, citing every supporting source;
   * several distinct values -> ``insufficient_information``, reporting each value with its
     source (contradictory evidence is surfaced, never resolved by guessing);
   * no value -> ``insufficient_information``;
   * no document could be retrieved -> ``task_failed``.

Retryable tool errors (timeout, rate limit, unavailable, malformed response) are retried up to
``max_retries`` times with linear backoff on the injected clock.

The regex stands in for language understanding. It makes behaviour fully reproducible, but
it means tasks must provide these variables; an LLM-backed agent would read the prompt instead.
"""

import re
from dataclasses import dataclass
from typing import Self

from pydantic import Field

from app.agents.interface import (
    AgentDescriptor,
    AgentEnvironment,
    AgentOutput,
    AgentRunMetadata,
    AgentTask,
    EvidenceItem,
    EvidenceStatus,
    FinalAnswer,
    StopReason,
)
from app.domain.base import DomainModel
from app.domain.enums import AgentOutcome
from app.tools.base import ToolError, ToolErrorCode, ToolFailure, ToolResult, ToolSuccess, output_as
from app.tools.documents import Document
from app.tools.fetch import FetchOutput
from app.tools.search import SearchOutput

SEARCH_TOOL = "search_documents"
FETCH_TOOL = "fetch_document"
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class TaskSpecificationError(ValueError):
    """The task lacks what this agent needs; the run cannot meaningfully start."""


@dataclass(frozen=True)
class ResearchQuestion:
    search_query: str
    answer_pattern: re.Pattern[str]
    subject: str

    @classmethod
    def from_task(cls, task: AgentTask) -> Self:
        missing_tools = sorted({SEARCH_TOOL, FETCH_TOOL} - set(task.available_tools))
        if missing_tools:
            raise TaskSpecificationError(f"task does not provide tools: {', '.join(missing_tools)}")

        query = task.variables.get("search_query")
        if not isinstance(query, str) or not query.strip():
            raise TaskSpecificationError("variable 'search_query' must be a non-empty string")

        raw_pattern = task.variables.get("answer_pattern")
        if not isinstance(raw_pattern, str):
            raise TaskSpecificationError("variable 'answer_pattern' must be a string")
        try:
            pattern = re.compile(raw_pattern, re.IGNORECASE)
        except re.error as exc:
            raise TaskSpecificationError(f"variable 'answer_pattern' is invalid: {exc}") from exc
        if pattern.groups != 1:
            raise TaskSpecificationError("variable 'answer_pattern' must have one capture group")

        subject = task.variables.get("subject", "the requested fact")
        if not isinstance(subject, str) or not subject.strip():
            raise TaskSpecificationError("variable 'subject' must be a non-empty string")
        return cls(search_query=query.strip(), answer_pattern=pattern, subject=subject.strip())

    def extract(self, document: Document) -> EvidenceItem:
        for sentence in _SENTENCE_BOUNDARY.split(document.content):
            match = self.answer_pattern.search(sentence)
            if match and match.group(1).strip():
                return EvidenceItem(
                    source_id=document.id, value=match.group(1).strip(), excerpt=sentence.strip()
                )
        return EvidenceItem(source_id=document.id, value=None)


class ResearchAgentConfig(DomainModel):
    max_sources: int = Field(default=3, ge=1, le=10)
    max_retries: int = Field(default=1, ge=0, le=5)
    retry_backoff_ms: int = Field(default=200, ge=0)


def _cite(source_ids: list[str]) -> str:
    return " ".join(f"[source:{source_id}]" for source_id in source_ids)


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


class DeterministicResearchAgent:
    def __init__(self, config: ResearchAgentConfig | None = None) -> None:
        self._config = config or ResearchAgentConfig()

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(name="deterministic-research", version="0.1.0")

    def run(self, task: AgentTask, environment: AgentEnvironment) -> AgentOutput:
        question = ResearchQuestion.from_task(task)

        environment.note(f"Searching for '{question.search_query}'.")
        search = self._call(
            environment,
            SEARCH_TOOL,
            {"query": question.search_query, "limit": self._config.max_sources},
        )
        if isinstance(search, ToolFailure):
            return self._failed(SEARCH_TOOL, search.error, evidence=[], unretrieved=[])

        hits = output_as(search, SearchOutput).results
        if not hits:
            environment.note("Search returned no documents.")
            return self._conclude(question, evidence=[], unretrieved=[])

        evidence: list[EvidenceItem] = []
        unretrieved: list[str] = []
        last_error: ToolError | None = None
        stop_reason = StopReason.COMPLETED
        for hit in hits[: self._config.max_sources]:
            fetched = self._call(environment, FETCH_TOOL, {"document_id": hit.document_id})
            if isinstance(fetched, ToolFailure):
                last_error = fetched.error
                unretrieved.append(hit.document_id)
                environment.note(f"Could not retrieve '{hit.document_id}' ({fetched.error.code}).")
                if fetched.error.code is ToolErrorCode.BUDGET_EXHAUSTED:
                    stop_reason = StopReason.BUDGET_EXHAUSTED
                    break
                continue
            item = question.extract(output_as(fetched, FetchOutput).document)
            environment.note(
                f"'{item.source_id}': "
                + (f"found '{item.value}'." if item.value else "no matching statement.")
            )
            evidence.append(item)

        if not evidence:
            if last_error is None:  # hits was non-empty, so at least one fetch failed
                raise RuntimeError("invariant violated: no evidence and no fetch error")
            return self._failed(FETCH_TOOL, last_error, evidence=[], unretrieved=unretrieved)
        return self._conclude(question, evidence, unretrieved, stop_reason)

    def _call(
        self, environment: AgentEnvironment, tool: str, arguments: dict[str, str | int]
    ) -> ToolResult:
        attempt = 0
        while True:
            result = environment.tools.call(tool, dict(arguments))
            if isinstance(result, ToolSuccess):
                return result
            error = result.error
            if not error.retryable or attempt >= self._config.max_retries:
                return result
            attempt += 1
            delay_ms = max(self._config.retry_backoff_ms * attempt, error.retry_after_ms or 0)
            environment.note(
                f"{tool} failed ({error.code}); retry {attempt}/{self._config.max_retries} "
                f"after {delay_ms} ms."
            )
            environment.clock.sleep(delay_ms / 1000)

    def _conclude(
        self,
        question: ResearchQuestion,
        evidence: list[EvidenceItem],
        unretrieved: list[str],
        stop_reason: StopReason = StopReason.COMPLETED,
    ) -> AgentOutput:
        groups: dict[str, tuple[str, list[str]]] = {}
        for item in evidence:
            if item.value is not None:
                _, sources = groups.setdefault(_normalise(item.value), (item.value, []))
                sources.append(item.source_id)

        missing_note = f" Note: could not retrieve {', '.join(unretrieved)}." if unretrieved else ""
        if not groups:
            consulted = ", ".join(item.source_id for item in evidence) or "none"
            answer = FinalAnswer(
                content=f"The available sources do not state {question.subject}. "
                f"Documents consulted: {consulted}.{missing_note}",
                declared_outcome=AgentOutcome.INSUFFICIENT_INFORMATION,
            )
            status = EvidenceStatus.INSUFFICIENT
        elif len(groups) == 1:
            ((value, sources),) = groups.values()
            subject = question.subject[0].upper() + question.subject[1:]
            answer = FinalAnswer(
                content=f"{subject}: {value} {_cite(sources)}.{missing_note}",
                declared_outcome=AgentOutcome.ANSWERED,
                source_ids=sources,
            )
            status = EvidenceStatus.CONSISTENT
        else:
            claims = "; ".join(f"{value} {_cite(sources)}" for value, sources in groups.values())
            answer = FinalAnswer(
                content=f"The sources disagree on {question.subject}: {claims}. "
                f"A definitive answer cannot be given from the available evidence.{missing_note}",
                declared_outcome=AgentOutcome.INSUFFICIENT_INFORMATION,
                source_ids=[s for _, sources in groups.values() for s in sources],
            )
            status = EvidenceStatus.CONFLICTING

        return AgentOutput(
            answer=answer,
            metadata=AgentRunMetadata(
                stop_reason=stop_reason,
                evidence_status=status,
                evidence=evidence,
                unretrieved_source_ids=unretrieved,
            ),
        )

    def _failed(
        self,
        tool: str,
        error: ToolError,
        *,
        evidence: list[EvidenceItem],
        unretrieved: list[str],
    ) -> AgentOutput:
        return AgentOutput(
            answer=FinalAnswer(
                content=f"Research could not be completed: {tool} failed "
                f"({error.code}: {error.message}).",
                declared_outcome=AgentOutcome.TASK_FAILED,
            ),
            metadata=AgentRunMetadata(
                stop_reason=StopReason.BUDGET_EXHAUSTED
                if error.code is ToolErrorCode.BUDGET_EXHAUSTED
                else StopReason.TOOL_FAILURE,
                evidence_status=EvidenceStatus.UNAVAILABLE,
                evidence=evidence,
                unretrieved_source_ids=unretrieved,
            ),
        )
