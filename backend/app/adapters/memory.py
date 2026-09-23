"""Реализации портов в памяти.

Нужны для тестов и чтобы логику ассистента можно было запустить до готовности БД.
Поведение совпадает с тем, что ожидается от реализации на PostgreSQL.
"""

import json
import re
import threading
from dataclasses import replace
from pathlib import Path

from app.modules.actions.models import Cart, Proposal, ProposalStatus, Receipt
from app.modules.actions.ports import CartVersionConflict
from app.modules.catalog.models import Product
from app.modules.catalog.quality import product_from_source

SYNTHETIC_CATALOG = Path(__file__).resolve().parents[3] / "data" / "synthetic" / "ekt_products.json"


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^\w,]+", text.lower().replace("ё", "е")) if t}


class InMemoryCatalog:
    def __init__(self, products: list[Product]):
        self._by_id = {p.id: p for p in products}

    @classmethod
    def from_file(cls, path: Path = SYNTHETIC_CATALOG) -> "InMemoryCatalog":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([product_from_source(raw) for raw in data["items"]])

    def replace_product(self, product: Product) -> None:
        self._by_id[product.id] = product

    def get_product(self, product_id: int, fresh: bool = False) -> Product | None:
        return self._by_id.get(product_id)

    def find_by_identifier(self, value: str) -> list[Product]:
        v = value.strip().lower()
        return [p for p in self._by_id.values()
                if v in {p.article.lower(), (p.supplier_article or "").lower()}]

    def search_text(self, query: str, limit: int = 20) -> list[Product]:
        q = _tokens(query)
        if not q:
            return []
        scored = []
        for p in self._by_id.values():
            name = _tokens(p.name)
            score = 3 * len(q & name) + len(q & _tokens(p.description))
            if score:
                scored.append((score, p.id, p))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [p for _, _, p in scored[:limit]]

    def list_category(self, category_path: tuple[str, ...], limit: int = 200) -> list[Product]:
        return [p for p in self._by_id.values() if p.category_path == category_path][:limit]


class InMemoryProposalStore:
    def __init__(self):
        self._items: dict[str, Proposal] = {}
        self._lock = threading.Lock()

    def save(self, proposal: Proposal) -> None:
        with self._lock:
            self._items[proposal.id] = replace(proposal)

    def get(self, session_id: str, proposal_id: str) -> Proposal | None:
        p = self._items.get(proposal_id)
        return replace(p) if p and p.session_id == session_id else None

    def open_for_session(self, session_id: str) -> list[Proposal]:
        found = [p for p in self._items.values()
                 if p.session_id == session_id and p.status is ProposalStatus.PROPOSED]
        return [replace(p) for p in sorted(found, key=lambda p: p.created_at, reverse=True)]

    def compare_and_set_status(self, proposal_id, expected, new, reason=None, receipt: Receipt | None = None) -> bool:
        with self._lock:
            p = self._items.get(proposal_id)
            if p is None or p.status is not expected:
                return False
            p.status = new
            p.status_reason = reason
            if receipt is not None:
                p.receipt = receipt
            return True


class InMemoryCartStore:
    def __init__(self):
        self._carts: dict[str, Cart] = {}
        self._applied: dict[str, Cart] = {}
        self._lock = threading.Lock()

    def get_cart(self, session_id: str) -> Cart:
        return self._carts.get(session_id, Cart(session_id, 0, {}))

    def apply_to_cart(self, session_id, additions, expected_version, operation_id) -> Cart:
        with self._lock:
            if operation_id in self._applied:
                return self._applied[operation_id]
            cart = self.get_cart(session_id)
            if cart.version != expected_version:
                raise CartVersionConflict()
            lines = dict(cart.lines)
            for pid, qty in additions.items():
                lines[pid] = lines.get(pid, 0) + qty
            new = Cart(session_id, cart.version + 1, lines)
            self._carts[session_id] = new
            self._applied[operation_id] = new
            return new


class InMemoryConversationStore:
    def __init__(self):
        self._messages: dict[str, list[dict]] = {}

    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        return list(self._messages.get(session_id, [])[-limit:])

    def append(self, session_id: str, role: str, content: str) -> None:
        self._messages.setdefault(session_id, []).append({"role": role, "content": content})
