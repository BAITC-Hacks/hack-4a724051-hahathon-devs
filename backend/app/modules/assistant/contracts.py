"""Что чат отдаёт наружу. Эти структуры HTTP-слой сериализует для фронтенда.

Цены, остатки, ссылки и состав предложения берутся из проверенных данных,
а не из текста модели. Фронтенд рисует карточки из ProductCard, а не парсит текст.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.modules.actions.models import Proposal
from app.modules.catalog.models import AttributeStatus, Product
from app.modules.catalog.quality import NON_SALE_STORES


@dataclass(frozen=True)
class ProductCard:
    id: int
    name: str
    article: str
    brand: str | None
    price: str | None  # десятичная строка в тенге
    currency: str
    unit: str
    stock_status: str
    sellable_quantity: int | None
    stores: list[dict]  # [{"name": "Алматы", "quantity": 5}] только продающие склады с остатком
    url: str
    image: str | None
    certificates: list[dict]
    attributes: list[dict]  # [{"label", "value", "conflict"}]
    warnings: list[str]

    @classmethod
    def from_product(cls, p: Product) -> "ProductCard":
        return cls(
            id=p.id, name=p.name, article=p.article, brand=p.brand,
            price=str(p.price) if p.price is not None else None, currency="KZT", unit=p.unit,
            stock_status=p.stock.status.value, sellable_quantity=p.stock.sellable_quantity,
            stores=[{"name": s.name, "quantity": s.quantity} for s in p.stock.stores
                    if s.quantity > 0 and s.name.strip().lower() not in NON_SALE_STORES],
            url=p.url, image=p.image,
            certificates=[asdict(c) for c in p.certificates],
            attributes=[{"label": a.label, "value": a.value, "conflict": a.status is AttributeStatus.CONFLICT}
                        for a in p.attributes],
            warnings=list(p.warnings),
        )


@dataclass(frozen=True)
class ProposalView:
    id: str
    status: str
    items: list[dict]
    total: str
    currency: str
    expires_at: str
    payload_hash: str
    cart_url: str | None  # только после применения

    @classmethod
    def from_proposal(cls, p: Proposal) -> "ProposalView":
        return cls(
            id=p.id, status=p.status.value,
            items=[{"product_id": i.product_id, "name": i.name, "article": i.article, "quantity": i.quantity,
                    "unit": i.unit, "unit_price": str(i.unit_price), "line_total": str(i.line_total)}
                   for i in p.items],
            total=str(p.total), currency="KZT", expires_at=p.expires_at.isoformat(),
            payload_hash=p.payload_hash, cart_url=p.receipt.cart_url if p.receipt else None,
        )


@dataclass
class ChatReply:
    message: str
    products: list[ProductCard] = field(default_factory=list)
    proposal: ProposalView | None = None
    sources: list[str] = field(default_factory=list)
    # "llm" | "fallback" (модель выключена/недоступна) | "action" (подтверждение без модели) | "verified_fallback"
    mode: str = "llm"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def money(value: Decimal) -> str:
    """16500 -> '16 500'."""
    q = value.quantize(Decimal(1)) if value == value.to_integral() else value
    return f"{q:,}".replace(",", " ")
