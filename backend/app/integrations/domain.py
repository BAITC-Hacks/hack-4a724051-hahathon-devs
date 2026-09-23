"""Explicit synthetic demo integration; no EKT calls and no paid model calls.

Catalog facts come from the participant-1 quality/search services. Cart writes
use SQLite transactions and survive API/worker restarts. Production stays gated.
"""
import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.adapters.memory import InMemoryCatalog
from app.contracts import (
    Attribute, Certificate, CartLine, CartSnapshot, Product, ProposalInput, ProposalView,
    SearchResult, SourceRef,
)
from app.core.errors import AppError
from app.modules.actions.models import Cart, Proposal, ProposalItem, ProposalStatus, Receipt
from app.modules.actions.ports import CartVersionConflict
from app.modules.actions.service import ActionService
from app.modules.catalog.models import AttributeStatus, StockStatus
from app.modules.catalog.quality import safe_url
from app.modules.search.alternatives import AlternativeService
from app.modules.search.service import SearchService


def product_view(product, source_ref: str = "synthetic_catalog", demo: bool = True) -> Product:
    source = SourceRef(ref=source_ref, path=f"/products/{product.id}",
                       fetched_at=product.fetched_at.isoformat())
    return Product(
        id=product.id, article_original=product.article, name=product.name,
        brand=product.brand, image_url=safe_url(product.image),
        category_path=list(product.category_path),
        price_amount=str(product.price) if product.price is not None else None,
        price_currency="KZT", stock_status={
            StockStatus.IN_STOCK: "available", StockStatus.OUT_OF_STOCK: "out_of_stock",
            StockStatus.NOT_SELLABLE: "out_of_stock", StockStatus.UNKNOWN: "unknown",
        }[product.stock.status],
        sellable_quantity=str(product.stock.sellable_quantity)
        if product.stock.sellable_quantity is not None else None,
        attributes=[Attribute(key=a.key, value=a.value, unit=a.unit,
                              status="conflict" if a.status is AttributeStatus.CONFLICT else "observed",
                              sources=[source]) for a in product.attributes],
        sources=[source], warnings=[*(["synthetic_demo_data"] if demo else []), *product.warnings],
        unit=product.unit, min_order=product.min_order,
        certificates=[Certificate(title=c.title, url="/api/v1" + c.url if c.url.startswith("/certificates/SYN-") else c.url,
                                  number=c.number, valid_until=c.valid_until)
                      for c in product.certificates],
    )


class DomainCatalogAdapter:
    """CatalogPort поверх любого CatalogReader: файл в памяти или PostgreSQL."""

    def __init__(self, catalog, source_ref: str = "synthetic_catalog", demo: bool = True,
                 coverage: str = "partial"):
        self.catalog = catalog
        self.source_ref, self.demo, self.coverage = source_ref, demo, coverage
        self.search_service = SearchService(catalog)
        self.alternative_service = AlternativeService(catalog)

    def _view(self, product) -> Product:
        return product_view(product, self.source_ref, self.demo)

    @property
    def _warnings(self) -> list[str]:
        return ["synthetic_demo_data"] if self.demo else []

    async def search(self, query: str, limit: int) -> SearchResult:
        matches = await asyncio.to_thread(self.search_service.search, query, limit)
        return SearchResult(items=[self._view(m.product) for m in matches],
                            coverage=self.coverage, warnings=self._warnings)

    async def get_product(self, product_id: int) -> Product:
        product = await asyncio.to_thread(self.catalog.get_product, product_id)
        if product is None:
            raise AppError("product_not_found", "Товар не найден.", 404)
        return self._view(product)

    async def alternatives(self, product_id: int) -> SearchResult:
        product = await asyncio.to_thread(self.catalog.get_product, product_id)
        if product is None:
            raise AppError("product_not_found", "Товар не найден.", 404)
        result = await asyncio.to_thread(self.alternative_service.find, product)
        reasons = [result.not_matched_reason] if result.not_matched_reason else []
        return SearchResult(items=[self._view(a.product) for a in result.alternatives],
                            coverage=self.coverage, warnings=[*self._warnings, *reasons, *result.blocked_by],
                            reasons={a.product.id: a.reason for a in result.alternatives})


SyntheticCatalogAdapter = DomainCatalogAdapter


def _encode(value):
    return json.dumps(asdict(value), default=str, ensure_ascii=False)


def _proposal(value):
    data = json.loads(value)
    data["items"] = tuple(ProposalItem(**{**item, "unit_price": Decimal(item["unit_price"])})
                          for item in data["items"])
    data["status"] = ProposalStatus(data["status"])
    for field in ("created_at", "expires_at"):
        data[field] = datetime.fromisoformat(data[field])
    if data["receipt"]:
        receipt = data["receipt"]
        data["receipt"] = Receipt(**{**receipt, "applied_at": datetime.fromisoformat(receipt["applied_at"])})
    return Proposal(**data)


class SQLiteDomainStores:
    """Transaction-scoped implementations of ProposalStore and CartStore.

    The caller holds BEGIN IMMEDIATE across validation and mutation. Proposal
    and cart changes therefore commit together, including receipt and replay.
    """
    def __init__(self, db):
        self.db = db

    def save(self, proposal):
        self.db.execute("INSERT INTO demo_proposals VALUES (?, ?, ?, ?)",
                        (proposal.id, proposal.session_id, _encode(proposal),
                         self.get_cart(proposal.session_id).version))

    def get(self, session_id, proposal_id):
        row = self.db.execute("SELECT payload FROM demo_proposals WHERE id=? AND session_id=?",
                              (proposal_id, session_id)).fetchone()
        return _proposal(row[0]) if row else None

    def open_for_session(self, session_id):
        items = [_proposal(r[0]) for r in self.db.execute(
            "SELECT payload FROM demo_proposals WHERE session_id=?", (session_id,))]
        return sorted((p for p in items if p.status is ProposalStatus.PROPOSED),
                      key=lambda p: p.created_at, reverse=True)

    def compare_and_set_status(self, proposal_id, expected, new, reason=None, receipt=None):
        row = self.db.execute("SELECT payload FROM demo_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            return False
        proposal = _proposal(row[0])
        if proposal.status is not expected:
            return False
        proposal.status, proposal.status_reason = new, reason
        if receipt:
            proposal.receipt = receipt
        # Outer transaction owns the SQLite write lock throughout this CAS.
        self.db.execute("UPDATE demo_proposals SET payload=? WHERE id=?", (_encode(proposal), proposal_id))
        return True

    def get_cart(self, session_id):
        row = self.db.execute("SELECT version, lines FROM demo_carts WHERE session_id=?", (session_id,)).fetchone()
        return Cart(session_id, row[0], {int(k): v for k, v in json.loads(row[1]).items()}) if row else Cart(session_id, 0)

    def apply_to_cart(self, session_id, additions, expected_version, operation_id):
        previous = self.db.execute("SELECT payload FROM demo_operations WHERE session_id=? AND operation_id=?",
                                   (session_id, operation_id)).fetchone()
        if previous:
            data = json.loads(previous[0])
            return Cart(session_id, data["version"], {int(k): v for k, v in data["lines"].items()})
        cart = self.get_cart(session_id)
        if cart.version != expected_version:
            raise CartVersionConflict()
        lines = dict(cart.lines)
        for pid, quantity in additions.items():
            lines[pid] = lines.get(pid, 0) + quantity
        cart = Cart(session_id, cart.version + 1, lines)
        self.db.execute("INSERT INTO demo_carts VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET version=excluded.version, lines=excluded.lines",
                        (session_id, cart.version, json.dumps(lines)))
        self.db.execute("INSERT INTO demo_operations VALUES (?, ?, ?)", (session_id, operation_id, _encode(cart)))
        return cart


class SQLiteDemoActions:
    def __init__(self, path: Path, catalog: InMemoryCatalog):
        self.path, self.catalog = Path(path), catalog
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS demo_proposals (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL, cart_version INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS demo_proposals_session ON demo_proposals(session_id);
                CREATE TABLE IF NOT EXISTS demo_carts (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL, lines TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS demo_operations (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    operation_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (session_id, operation_id));
                CREATE TABLE IF NOT EXISTS demo_requests (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, proposal_id TEXT NOT NULL,
                    PRIMARY KEY (session_id, kind, key));
            """)

    def _view(self, store, proposal):
        base_version = store.db.execute("SELECT cart_version FROM demo_proposals WHERE id=? AND session_id=?",
                                        (proposal.id, proposal.session_id)).fetchone()[0]
        return ProposalView(
            id=UUID(proposal.id), version=1, status=proposal.status.value, mode="demo",
            items=[CartLine(product_id=i.product_id, quantity=i.quantity, article_original=i.article,
                            name=i.name, unit_price_amount=str(i.unit_price), line_total_amount=str(i.line_total))
                   for i in proposal.items], total_amount=str(proposal.total), currency="KZT",
            cart_version=base_version + 1, expires_at=proposal.expires_at.timestamp(),
            receipt_id=UUID(proposal.receipt.operation_id) if proposal.receipt else None,
            cart_url=proposal.receipt.cart_url if proposal.receipt else None,
        )

    def _run(self, session_id, kind, proposal_id=None, payload=None, version=None, key=None):
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND expires_at>?",
                              (session_id, time.time())).fetchone():
                raise AppError("session_expired", "Сессия истекла.", 401)
            store = SQLiteDomainStores(db)
            service = ActionService(self.catalog, store, store, cart_url="/api/v1/cart/view")
            fingerprint = hashlib.sha256(json.dumps(
                [proposal_id, version, payload.model_dump() if payload else None], sort_keys=True).encode()).hexdigest()
            if key:
                replay = db.execute("SELECT fingerprint, proposal_id FROM demo_requests WHERE session_id=? AND kind=? AND key=?",
                                    (session_id, kind, key)).fetchone()
                if replay:
                    if replay[0] != fingerprint:
                        raise AppError("idempotency_conflict", "Ключ уже использован с другими данными.", 409)
                    return self._view(store, store.get(session_id, replay[1]))
            if kind == "cart":
                cart = store.get_cart(session_id)
                lines = []
                for pid, quantity in cart.lines.items():
                    product = self.catalog.get_product(pid, fresh=True)
                    if product is None or product.price is None:
                        raise AppError("cart_price_unknown", "Цена позиции корзины не подтверждена.", 409)
                    lines.append(CartLine(product_id=pid, quantity=quantity, article_original=product.article,
                                          name=product.name, unit_price_amount=str(product.price),
                                          line_total_amount=str(product.price * quantity)))
                return CartSnapshot(mode="demo", version=cart.version + 1, items=lines,
                                    total_amount=str(sum((Decimal(i.line_total_amount) for i in lines), Decimal(0))),
                                    currency="KZT")
            if kind == "propose":
                result = service.prepare_proposal(session_id, [(i.product_id, i.quantity) for i in payload.items])
                proposal = result.proposal
                if proposal is None:
                    code = result.problems[0].code if result.problems else "invalid_proposal"
                    raise AppError(code, result.message + " " + " ".join(p.message for p in result.problems), 409)
            else:
                proposal = store.get(session_id, proposal_id)
                if proposal is None:
                    raise AppError("proposal_not_found", "Предложение не найдено.", 404)
                if kind == "confirm":
                    if version != 1:
                        raise AppError("proposal_version_conflict", "Версия предложения изменилась.", 409)
                    base = db.execute("SELECT cart_version FROM demo_proposals WHERE id=?", (proposal_id,)).fetchone()[0]
                    if proposal.status is ProposalStatus.PROPOSED and store.get_cart(session_id).version != base:
                        store.compare_and_set_status(proposal_id, ProposalStatus.PROPOSED, ProposalStatus.STALE,
                                                     reason="cart_changed")
                        proposal = store.get(session_id, proposal_id)
                    else:
                        proposal = service.confirm_and_apply(session_id, proposal_id, proposal.payload_hash).proposal
                elif kind == "reject":
                    proposal = service.reject(session_id, proposal_id).proposal
            if key:
                db.execute("INSERT INTO demo_requests VALUES (?, ?, ?, ?, ?)",
                           (session_id, kind, key, fingerprint, proposal.id))
            return self._view(store, proposal)

    async def get_cart(self, session_id):
        return await asyncio.to_thread(self._run, session_id, "cart")

    async def propose(self, session_id, payload: ProposalInput, key):
        return await asyncio.to_thread(self._run, session_id, "propose", payload=payload, key=key)

    async def get_proposal(self, session_id, proposal_id):
        return await asyncio.to_thread(self._run, session_id, "get", proposal_id=UUID(proposal_id).hex)

    async def confirm(self, session_id, proposal_id, version, key):
        return await asyncio.to_thread(self._run, session_id, "confirm", proposal_id=UUID(proposal_id).hex,
                                       version=version, key=key)

    async def reject(self, session_id, proposal_id):
        return await asyncio.to_thread(self._run, session_id, "reject", proposal_id=UUID(proposal_id).hex)
