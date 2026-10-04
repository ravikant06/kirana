"""
The API's request and response shapes (Pydantic) — the DTOs.

These are what docs/ai-contract.md describes, and the only shapes the API
exposes. The SQLAlchemy models in kirana_ai/db/ are the table rows; routes
convert between the two, so a new column never leaks into the API by accident
(Kirana's rule: controllers return DTOs, never entities).
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # an unknown field is a 400, not silently ignored

    thread_id: uuid.UUID | None = None
    # Stripped first, so a message of only spaces fails min_length.
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                              max_length=MAX_MESSAGE_CHARS)]


class Citation(BaseModel):
    source: str
    doc_id: str
    title: str | None = None
    pages: list[int] = Field(default_factory=list)   # PDFs: pages of the retrieved chunks


class Step(BaseModel):
    """One tool call the agent made, as shown in the Requests panel."""
    tool: str
    query: str = ""
    where: dict[str, Any] = Field(default_factory=dict)   # filters the model chose
    count: int                                             # hits (or documents) returned
    below_floor: int = 0                                   # hits dropped by the relevance floor


class Usage(BaseModel):
    llm_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    # Streaming only: time from the start of the agent to the first answer token.
    first_token_ms: int | None = None
    # Sources named in the reply that were not retrieved this turn: never shown as citations.
    unverified_sources: list[str] = Field(default_factory=list)
    # A decimal string ("0.004170"), never a float: money is exact. null = unknown price.
    cost_usd: Decimal | None


class ChatReply(BaseModel):
    thread_id: uuid.UUID
    message_id: uuid.UUID
    reply: str
    citations: list[Citation]
    steps: list[Step]
    usage: Usage


class ThreadSummary(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation]
    steps: list[Step]
    created_at: datetime


class ThreadDetail(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


# --- Knowledge base (Phase 2) -----------------------------------------------------

DocTypeName = Literal["policy", "faq", "guide"]
DocStatusName = Literal["pending", "uploaded", "indexing", "ready", "failed", "deleting", "deleted"]


class KbUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    doc_type: DocTypeName
    file_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    # Only what the ingest worker can parse. The signed policy pins this exact value.
    content_type: Literal["application/pdf", "text/markdown", "text/plain"]
    size_bytes: int = Field(ge=1)   # upper bound checked against config in the route


class KbUploadTicket(BaseModel):
    """Same shape as Kirana's image UploadTicket: the browser posts form_fields + the file to upload_url."""
    document_id: uuid.UUID
    object_key: str
    upload_url: str
    form_fields: dict[str, str]
    expires_at: datetime


class KbDocument(BaseModel):
    id: uuid.UUID
    title: str
    doc_type: DocTypeName
    file_name: str
    content_type: str
    size_bytes: int | None
    status: DocStatusName
    page_count: int | None
    chunk_count: int | None
    error: str | None
    uploaded_by: str
    created_at: datetime
    updated_at: datetime
