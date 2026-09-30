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


class Step(BaseModel):
    """One tool call the agent made, as shown in the Requests panel."""
    tool: str
    query: str = ""
    where: dict[str, Any] = Field(default_factory=dict)   # filters the model chose
    count: int                                             # hits (or documents) returned


class Usage(BaseModel):
    llm_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
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
