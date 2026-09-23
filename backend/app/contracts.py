"""Shared integration DTOs. No database, HTTP or model SDK dependencies."""
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Meta(DTO):
    request_id: str
    warnings: list[str] = Field(default_factory=list)


class Envelope(DTO, Generic[T]):
    data: T
    meta: Meta


class ErrorDetail(DTO):
    code: str
    message: str
    retryable: bool


class ErrorEnvelope(DTO):
    error: ErrorDetail
    meta: Meta


class SourceRef(DTO):
    ref: str
    path: str
    fetched_at: str


class Attribute(DTO):
    key: str
    value: str | None = None
    unit: str | None = None
    status: Literal["observed", "verified", "conflict", "unknown"]
    sources: list[SourceRef] = Field(default_factory=list)


class Product(DTO):
    id: int = Field(gt=0)
    article_original: str
    name: str
    category_path: list[str] | None = None
    price_amount: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    price_currency: str | None = None
    stock_status: Literal["available", "out_of_stock", "unknown"] = "unknown"
    sellable_quantity: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    attributes: list[Attribute] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SearchResult(DTO):
    items: list[Product] = Field(default_factory=list, max_length=20)
    coverage: Literal["partial", "complete", "unknown"] = "unknown"
    warnings: list[str] = Field(default_factory=list)


class ParsedDocument(DTO):
    asset_id: UUID
    status: Literal["ready", "partial"]
    text: str = Field(max_length=40000)
    warnings: list[str] = Field(default_factory=list)


class CartItem(DTO):
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=100000, strict=True)


class ProposalInput(DTO):
    items: list[CartItem] = Field(min_length=1, max_length=50)


class ConfirmationInput(DTO):
    version: int = Field(ge=1, strict=True)


class CartLine(CartItem):
    article_original: str
    name: str
    unit_price_amount: str = Field(pattern=r"^\d+(\.\d+)?$")
    line_total_amount: str = Field(pattern=r"^\d+(\.\d+)?$")


class CartSnapshot(DTO):
    mode: Literal["demo", "real"]
    version: int = Field(ge=1)
    items: list[CartLine] = Field(default_factory=list)
    total_amount: str = Field(pattern=r"^\d+(\.\d+)?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProposalView(DTO):
    id: UUID
    version: int = Field(ge=1)
    status: Literal[
        "proposed", "queued", "executing", "applied", "rejected",
        "expired", "stale", "failed", "outcome_unknown",
    ]
    mode: Literal["demo", "real"]
    items: list[CartLine] = Field(min_length=1, max_length=50)
    total_amount: str = Field(pattern=r"^\d+(\.\d+)?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cart_version: int = Field(ge=1)
    expires_at: float
    receipt_id: UUID | None = None
    # Same-origin path only. Real external checkout requires a separate vetted contract.
    cart_url: str | None = Field(default=None, pattern=r"^/[A-Za-z0-9/_?=&.%~-]*$")

    @field_validator("cart_url")
    @classmethod
    def same_origin_path(cls, value):
        if value is not None and value.startswith("//"):
            raise ValueError("Cart URL must be a same-origin path")
        return value
