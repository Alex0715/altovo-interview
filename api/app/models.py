from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DocumentStatus = Literal["parsing", "embedding", "ready", "failed"]


class DocumentSummary(BaseModel):
    id: UUID
    filename: str
    mime_type: str
    byte_size: int
    page_count: int | None = None
    chunk_count: int = 0
    status: DocumentStatus
    error: str | None = None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    # Source of truth for citation highlighting: char offsets index into this.
    full_text: str | None = None


class Chunk(BaseModel):
    id: UUID
    document_id: UUID
    ordinal: int
    content: str
    token_count: int
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int


class RetrievedSource(BaseModel):
    """A chunk selected for the prompt. `marker` is the [n] the model sees."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    snippet: str
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None


class Citation(BaseModel):
    """A citation that survived server-side validation against retrieved chunks."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    filename: str
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[UUID] | None = None  # None = search everything


class AnswerPayload(BaseModel):
    text: str
    citations: list[Citation]
    abstained: bool
    abstain_reason: Literal["below_floor", "model_declined", "no_documents"] | None = None
    assumption: str | None = None


class LLMAnswer(BaseModel):
    """The JSON contract we ask the model for. Treated as untrusted input:
    markers are range-checked against retrieved chunks before use."""

    answer: str = ""
    citations: list[int] = Field(default_factory=list)
    insufficient_context: bool = False
    assumption: str | None = None
