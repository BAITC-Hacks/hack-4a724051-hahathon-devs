"""Предложения в корзину и их подтверждение.

prepare_proposal() только показывает, что будет добавлено. Корзина меняется
исключительно в confirm_and_apply(), который вызывается по кнопке или по
точной фразе подтверждения. LLM этот модуль не вызывает.
"""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.modules.actions.models import (
    ActionResult, ItemProblem, Proposal, ProposalItem, ProposalStatus, Receipt,
)
from app.modules.actions.ports import CartStore, CartVersionConflict, ProposalStore
from app.modules.catalog.models import Product, StockStatus
from app.modules.catalog.ports import CatalogReader

PROPOSAL_TTL = timedelta(minutes=15)
MAX_ITEMS = 20
MAX_QUANTITY = 10_000

# Только однозначные фразы. Всё остальное не считается подтверждением.
CONFIRM_PHRASES = {
    "да", "да добавь", "да добавляй", "да добавьте", "добавь", "добавьте", "добавляй",
    "да добавь в корзину", "добавь в корзину", "добавьте в корзину", "да в корзину",
    "подтверждаю", "да подтверждаю", "ок добавь", "ok добавь", "yes", "yes add",
    "иә", "иә қос", "қос", "себетке қос", "иә себетке қос", "растаймын",
}
REJECT_PHRASES = {
    "нет", "не надо", "нет не надо", "нет спасибо", "не нужно", "не добавляй", "отмена", "отмени", "no",
    "жоқ", "керек емес",
}


def normalize_reply(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е"))
    return " ".join(text.split())


def is_explicit_confirmation(text: str) -> bool:
    return normalize_reply(text) in CONFIRM_PHRASES


def is_explicit_rejection(text: str) -> bool:
    return normalize_reply(text) in REJECT_PHRASES


def _payload_hash(items: tuple[ProposalItem, ...]) -> str:
    body = [(i.product_id, i.quantity, str(i.unit_price)) for i in items]
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ActionService:
    def __init__(self, catalog: CatalogReader, proposals: ProposalStore, carts: CartStore, cart_url: str):
        self.catalog = catalog
        self.proposals = proposals
        self.carts = carts
        self.cart_url = cart_url

    def _check_item(self, product: Product | None, product_id: int, quantity, in_cart: int) -> ItemProblem | None:
        if product is None:
            return ItemProblem(product_id, "not_found", f"Товар {product_id} не найден в каталоге.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or quantity > MAX_QUANTITY:
            return ItemProblem(product_id, "bad_quantity",
                               f"Количество для «{product.name}» должно быть целым положительным числом.")
        if product.price is None:
            return ItemProblem(product_id, "price_unknown", f"Для «{product.name}» нет подтверждённой цены.")
        if product.stock.status is StockStatus.UNKNOWN:
            return ItemProblem(product_id, "stock_unknown",
                               f"Не удалось проверить остаток «{product.name}», добавить сейчас нельзя.")
        available = product.stock.sellable_quantity or 0
        if available <= 0:
            return ItemProblem(product_id, "out_of_stock", f"«{product.name}» сейчас нет в наличии.")
        if in_cart + quantity > available:
            left = max(available - in_cart, 0)
            return ItemProblem(product_id, "exceeds_stock",
                               f"«{product.name}»: в наличии {available} {product.unit}, в корзине уже {in_cart}, "
                               f"можно добавить не больше {left}.")
        if product.min_order and quantity < product.min_order:
            return ItemProblem(product_id, "below_min",
                               f"«{product.name}» продаётся от {product.min_order} {product.unit}.")
        return None

    def prepare_proposal(self, session_id: str, items: list[tuple[int, int]]) -> ActionResult:
        if not items or len(items) > MAX_ITEMS:
            return ActionResult(False, None, (), "Нужно от 1 до 20 позиций.")
        merged: dict[int, int] = {}
        for product_id, quantity in items:
            if not isinstance(quantity, int) or isinstance(quantity, bool):
                merged[product_id] = quantity
                continue
            merged[product_id] = merged.get(product_id, 0) + quantity

        cart = self.carts.get_cart(session_id)
        lines, problems = [], []
        for product_id, quantity in merged.items():
            product = self.catalog.get_product(product_id, fresh=True)
            problem = self._check_item(product, product_id, quantity, cart.lines.get(product_id, 0))
            if problem:
                problems.append(problem)
                continue
            lines.append(ProposalItem(product.id, product.name, product.article, quantity, product.unit,
                                      product.price))
        if problems:
            return ActionResult(False, None, tuple(problems), "Предложение не создано.")

        # Открытым остаётся одно предложение, старые снимаем, чтобы «да» относилось к последнему.
        for old in self.proposals.open_for_session(session_id):
            self.proposals.compare_and_set_status(old.id, ProposalStatus.PROPOSED, ProposalStatus.EXPIRED,
                                                  reason="superseded")
        now = _now()
        items_t = tuple(lines)
        proposal = Proposal(
            id=uuid.uuid4().hex, session_id=session_id, items=items_t, status=ProposalStatus.PROPOSED,
            created_at=now, expires_at=now + PROPOSAL_TTL, payload_hash=_payload_hash(items_t),
        )
        self.proposals.save(proposal)
        return ActionResult(True, proposal, (), "Предложение создано, корзина не изменена.")

    def current_proposal(self, session_id: str) -> Proposal | None:
        open_ = self.proposals.open_for_session(session_id)
        return open_[0] if len(open_) == 1 else None

    def reject(self, session_id: str, proposal_id: str) -> ActionResult:
        proposal = self.proposals.get(session_id, proposal_id)
        if proposal is None:
            return ActionResult(False, None, (), "Предложение не найдено.")
        if self.proposals.compare_and_set_status(proposal.id, ProposalStatus.PROPOSED, ProposalStatus.REJECTED):
            return ActionResult(True, self.proposals.get(session_id, proposal_id), (), "Хорошо, корзину не трогаю.")
        return ActionResult(False, proposal, (), "Это предложение уже не активно.")

    def confirm_and_apply(self, session_id: str, proposal_id: str, payload_hash: str | None = None) -> ActionResult:
        proposal = self.proposals.get(session_id, proposal_id)
        if proposal is None:
            return ActionResult(False, None, (), "Предложение не найдено.")
        if proposal.status is ProposalStatus.APPLIED:
            return ActionResult(True, proposal, (), "Уже добавлено в корзину.")
        if proposal.status is not ProposalStatus.PROPOSED:
            return ActionResult(False, proposal, (), "Это предложение уже не активно, нужно новое.")
        if payload_hash is not None and payload_hash != proposal.payload_hash:
            return ActionResult(False, proposal, (), "Состав предложения не совпадает с показанным.")
        if _now() > proposal.expires_at:
            self.proposals.compare_and_set_status(proposal.id, ProposalStatus.PROPOSED, ProposalStatus.EXPIRED,
                                                  reason="ttl")
            return ActionResult(False, self.proposals.get(session_id, proposal_id), (),
                                "Предложение устарело, давайте соберу новое.")

        # Перепроверяем цену и остаток по свежим данным.
        cart = self.carts.get_cart(session_id)
        problems = []
        for item in proposal.items:
            product = self.catalog.get_product(item.product_id, fresh=True)
            problem = self._check_item(product, item.product_id, item.quantity, cart.lines.get(item.product_id, 0))
            if problem is None and product.price != item.unit_price:
                problem = ItemProblem(item.product_id, "price_changed",
                                      f"Цена «{item.name}» изменилась: было {item.unit_price}, стало {product.price}.")
            if problem:
                problems.append(problem)
        if problems:
            self.proposals.compare_and_set_status(proposal.id, ProposalStatus.PROPOSED, ProposalStatus.STALE,
                                                  reason=problems[0].code)
            return ActionResult(False, self.proposals.get(session_id, proposal_id), tuple(problems),
                                "Данные изменились, корзина не изменена.")

        # Захватываем предложение, чтобы два подтверждения не применились дважды.
        if not self.proposals.compare_and_set_status(proposal.id, ProposalStatus.PROPOSED, ProposalStatus.EXECUTING):
            current = self.proposals.get(session_id, proposal_id)
            ok = current is not None and current.status is ProposalStatus.APPLIED
            return ActionResult(ok, current, (), "Уже добавлено в корзину." if ok else "Предложение уже обрабатывается.")

        additions = {i.product_id: i.quantity for i in proposal.items}
        try:
            new_cart = self.carts.apply_to_cart(session_id, additions, cart.version, operation_id=proposal.id)
        except CartVersionConflict:
            self.proposals.compare_and_set_status(proposal.id, ProposalStatus.EXECUTING, ProposalStatus.STALE,
                                                  reason="cart_changed")
            return ActionResult(False, self.proposals.get(session_id, proposal_id), (),
                                "Корзину изменили параллельно, соберу предложение заново.")
        except Exception:
            self.proposals.compare_and_set_status(proposal.id, ProposalStatus.EXECUTING, ProposalStatus.FAILED,
                                                  reason="cart_error")
            raise

        receipt = Receipt(proposal.id, proposal.id, new_cart.version, self.cart_url, _now())
        self.proposals.compare_and_set_status(proposal.id, ProposalStatus.EXECUTING, ProposalStatus.APPLIED,
                                              receipt=receipt)
        return ActionResult(True, self.proposals.get(session_id, proposal_id), (), "Добавлено в корзину.")
