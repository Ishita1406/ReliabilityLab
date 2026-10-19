"""Mock document fetch from a ``DocumentCorpus``."""

from app.domain.base import DomainModel
from app.domain.ids import Slug
from app.runtime.clock import Clock
from app.tools.base import ToolError, ToolErrorCode, ToolExecutionError
from app.tools.documents import Document, DocumentCorpus
from app.tools.mock import MockScript, MockTool


class FetchInput(DomainModel):
    document_id: Slug


class FetchOutput(DomainModel):
    document: Document


class MockFetchTool(MockTool[FetchInput, FetchOutput]):
    name = "fetch_document"
    description = "Fetch the full content of a document by ID."
    input_model = FetchInput
    output_model = FetchOutput

    def __init__(
        self,
        corpus: DocumentCorpus,
        clock: Clock,
        script: MockScript | None = None,
        latency_ms: int = 5,
    ) -> None:
        super().__init__(clock, script, latency_ms)
        self._corpus = corpus

    def respond(self, arguments: FetchInput) -> FetchOutput:
        document = self._corpus.get(arguments.document_id)
        if document is None:
            raise ToolExecutionError(
                ToolError.create(
                    ToolErrorCode.NOT_FOUND, f"document '{arguments.document_id}' does not exist"
                )
            )
        return FetchOutput(document=document)
