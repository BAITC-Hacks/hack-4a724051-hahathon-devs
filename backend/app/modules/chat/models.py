from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.contracts import DTO, Product, ProposalView


class TurnInput(DTO):
    text: str = Field(min_length=1, max_length=8000)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    language: Literal["auto", "ru", "kk", "en"] = "auto"
    page_product_id: int | None = Field(default=None, gt=0, strict=True)
    allow_external_analysis: bool = False

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 8000:
            raise ValueError("Text must contain 1..8000 UTF-8 bytes")
        return value


class AssistantOutput(DTO):
    reference_product_id: int | None = Field(default=None, gt=0)
    message: str = Field(max_length=8000)
    products: list[Product] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    mode: Literal["catalog_only", "unavailable", "grounded", "action"]
    language: Literal["ru", "kk", "en"] = "ru"
    proposal: ProposalView | None = None
    sources: list[str] = Field(default_factory=list, max_length=30)
    alternative_reasons: dict[int, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class TurnView(DTO):
    id: UUID
    conversation_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    output: AssistantOutput | None = None
    error_code: str | None = None
    created_at: float


class TurnSubmission(DTO):
    turn: TurnView
    created: bool


class ConversationView(DTO):
    id: UUID
    created_at: float


class MessageView(DTO):
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    created_at: float


class SessionView(DTO):
    csrf_token: str
    expires_at: float


@dataclass(frozen=True)
class Session:
    id: str
    expires_at: float


@dataclass(frozen=True)
class ClaimedJob:
    turn_id: str
    session_id: str
    conversation_id: str
    lease_token: str
    payload: TurnInput
