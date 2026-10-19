"""Mock keyword search over a ``DocumentCorpus``.

Ranking is deterministic: score = fraction of distinct query terms that appear in the
document's title or content; ties are broken by document ID. Documents with score 0 are not
returned.
"""

import re
from typing import Annotated

from pydantic import Field, StringConstraints

from app.domain.base import DomainModel, NonEmptyStr
from app.domain.ids import Slug
from app.runtime.clock import Clock
from app.tools.documents import DocumentCorpus
from app.tools.mock import MockScript, MockTool

STOPWORDS = frozenset(
    {"a", "an", "and", "the", "of", "in", "on", "to", "for", "is", "was", "what", "when", "which"}
)
SNIPPET_LENGTH = 160


def terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS}


class SearchInput(DomainModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    limit: int = Field(default=5, ge=1, le=20)


class SearchHit(DomainModel):
    document_id: Slug
    title: NonEmptyStr
    score: float = Field(gt=0.0, le=1.0)
    snippet: str


class SearchOutput(DomainModel):
    results: list[SearchHit]


class MockSearchTool(MockTool[SearchInput, SearchOutput]):
    name = "search_documents"
    description = "Keyword search over the document collection; returns ranked document IDs."
    input_model = SearchInput
    output_model = SearchOutput

    def __init__(
        self,
        corpus: DocumentCorpus,
        clock: Clock,
        script: MockScript | None = None,
        latency_ms: int = 5,
    ) -> None:
        super().__init__(clock, script, latency_ms)
        self._corpus = corpus

    def respond(self, arguments: SearchInput) -> SearchOutput:
        query_terms = terms(arguments.query)
        hits: list[SearchHit] = []
        if query_terms:
            for doc in self._corpus.documents:
                matched = query_terms & terms(f"{doc.title} {doc.content}")
                if matched:
                    hits.append(
                        SearchHit(
                            document_id=doc.id,
                            title=doc.title,
                            score=len(matched) / len(query_terms),
                            snippet=doc.content[:SNIPPET_LENGTH],
                        )
                    )
        hits.sort(key=lambda hit: (-hit.score, hit.document_id))
        return SearchOutput(results=hits[: arguments.limit])
