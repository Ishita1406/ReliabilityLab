"""Synthetic document corpus served by the mock search and fetch tools."""

from typing import Self

from pydantic import Field, model_validator

from app.domain.base import DomainModel, NonEmptyStr, require_unique
from app.domain.benchmark import SourceDocument
from app.domain.ids import Slug


class Document(DomainModel):
    id: Slug
    title: NonEmptyStr
    content: NonEmptyStr


class DocumentCorpus(DomainModel):
    documents: list[Document] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        require_unique((d.id for d in self.documents), "document ids")
        return self

    @classmethod
    def from_source_documents(cls, documents: list[SourceDocument]) -> Self:
        """Build a corpus from a benchmark case's ``inputs.documents``."""
        return cls(
            documents=[Document(id=d.id, title=d.title, content=d.content) for d in documents]
        )

    def get(self, document_id: str) -> Document | None:
        return next((d for d in self.documents if d.id == document_id), None)
