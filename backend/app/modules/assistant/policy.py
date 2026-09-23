"""Проверка ответа модели перед показом клиенту.

Модель может ошибиться в цифре или придумать ссылку. Здесь каждая цена, остаток,
ссылка и заявление про корзину сверяются с фактами, которые вернули инструменты.
Если что-то не сходится, ответ модели не показываем и собираем текст из фактов.
"""

import re
from decimal import Decimal, InvalidOperation

from app.modules.assistant.tools import Facts
from app.modules.catalog.quality import NON_SALE_STORES

MONEY = re.compile(r"(\d[\d\s  ]*(?:[.,]\d+)?)\s*(?:₸|тг\b|тенге|kzt)", re.I)
STOCK_BEFORE = re.compile(r"(?:в наличии|остаток|осталось|доступно|на складе)[^\d\n]{0,20}(\d+)\s*(?:шт|м\b)", re.I)
STOCK_AFTER = re.compile(r"(\d+)\s*(?:шт|м)\.?\s*(?:в наличии|на складе|осталось)", re.I)
URL = re.compile(r"https?://[^\s)\]>»\"']+|(?<![\w/])/(?:catalog|certificates|cart)/[^\s)\]>»\"']*")
CART_CLAIM = re.compile(r"(добавил[аи]?|добавлен[аоы]?|положил[аи]?|қостым|қосылды)\s+(?:\S+\s+){0,3}?(?:в\s+корзин|себет)",
                        re.I)
NUMBER = re.compile(r"\d[\d\s  ]*(?:[.,]\d+)?")


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[\s  ]", "", raw).replace(",", "."))
    except InvalidOperation:
        return None


def _allowed_money(facts: Facts) -> set[Decimal]:
    allowed = {p.price for p in facts.products.values() if p.price is not None}
    if facts.proposal:
        allowed.add(facts.proposal.total)
        allowed.update(i.line_total for i in facts.proposal.items)
    for text in facts.terms_text:
        allowed.update(d for d in (_to_decimal(n) for n in NUMBER.findall(text)) if d is not None)
    return allowed


def _allowed_stock(facts: Facts) -> set[int]:
    allowed = set()
    for p in facts.products.values():
        if p.stock.sellable_quantity is not None:
            allowed.add(p.stock.sellable_quantity)
        allowed.update(s.quantity for s in p.stock.stores if s.name.strip().lower() not in NON_SALE_STORES)
    if facts.proposal:
        allowed.update(i.quantity for i in facts.proposal.items)
    return allowed


def _allowed_urls(facts: Facts, cart_url: str) -> set[str]:
    allowed = {cart_url, *facts.sources}
    for p in facts.products.values():
        allowed.add(p.url)
        allowed.update(c.url for c in p.certificates)
    return {u.rstrip("/") for u in allowed if u}


def find_violations(text: str, facts: Facts, cart_url: str, cart_applied: bool = False) -> list[str]:
    problems = []
    money = _allowed_money(facts)
    for m in MONEY.finditer(text):
        value = _to_decimal(m.group(1))
        if value is not None and value not in money:
            problems.append(f"unverified_price:{m.group(0).strip()}")

    stock = _allowed_stock(facts)
    for pattern in (STOCK_BEFORE, STOCK_AFTER):
        for m in pattern.finditer(text):
            if int(m.group(1)) not in stock:
                problems.append(f"unverified_stock:{m.group(0).strip()}")

    urls = _allowed_urls(facts, cart_url)
    for m in URL.finditer(text):
        url = m.group(0).rstrip(".,;:!?").rstrip("/")
        if url not in urls:
            problems.append(f"unknown_url:{url}")

    if not cart_applied:
        for m in CART_CLAIM.finditer(text):
            before = text[max(0, m.start() - 20):m.start()].lower()
            if not any(w in before for w in ("будет", "будут", "не ", "можно", "могу", "после", "чтобы", "если")):
                problems.append("claims_cart_changed")
    return problems
