from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"  # показано клиенту, корзина не менялась
    EXECUTING = "executing"  # подтверждено, идёт запись в корзину
    APPLIED = "applied"
    REJECTED = "rejected"
    EXPIRED = "expired"  # истёк срок или заменено новым предложением
    STALE = "stale"  # цена или остаток изменились, нужно новое предложение
    FAILED = "failed"


@dataclass(frozen=True)
class ProposalItem:
    product_id: int
    name: str
    article: str
    quantity: int
    unit: str
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Receipt:
    operation_id: str
    proposal_id: str
    cart_version: int
    cart_url: str
    applied_at: datetime


@dataclass
class Proposal:
    id: str
    session_id: str
    items: tuple[ProposalItem, ...]
    status: ProposalStatus
    created_at: datetime
    expires_at: datetime
    payload_hash: str  # hash состава, чтобы подтверждение шло ровно на показанное
    receipt: Receipt | None = None
    status_reason: str | None = None

    @property
    def total(self) -> Decimal:
        return sum((i.line_total for i in self.items), Decimal(0))


@dataclass(frozen=True)
class Cart:
    session_id: str
    version: int
    lines: dict[int, int] = field(default_factory=dict)  # product_id -> количество


@dataclass(frozen=True)
class ItemProblem:
    product_id: int | None
    code: str  # not_found, out_of_stock, exceeds_stock, bad_quantity, price_unknown, stock_unknown, below_min
    message: str


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    proposal: Proposal | None
    problems: tuple[ItemProblem, ...] = ()
    message: str = ""
