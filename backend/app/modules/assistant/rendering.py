"""Тексты, которые собираются из фактов без модели."""

from app.modules.actions.models import Proposal
from app.modules.assistant.contracts import money
from app.modules.catalog.models import Product, StockStatus
from app.modules.catalog.quality import NON_SALE_STORES


def stock_line(p: Product) -> str:
    if p.stock.status is StockStatus.IN_STOCK:
        stores = [f"{s.name} {s.quantity}" for s in p.stock.stores
                  if s.quantity > 0 and s.name.strip().lower() not in NON_SALE_STORES]
        return f"в наличии {p.stock.sellable_quantity} {p.unit}" + (f" ({', '.join(stores[:5])})" if stores else "")
    if p.stock.status is StockStatus.UNKNOWN:
        return "наличие сейчас не удалось проверить"
    return "нет в наличии"


def product_line(p: Product) -> str:
    price = f"{money(p.price)} ₸" if p.price is not None else "цена не указана"
    line = f"• {p.name} (арт. {p.article}): {price}, {stock_line(p)}."
    if p.certificates:
        c = p.certificates[0]
        line += f" Сертификат: {c.number or c.title}, {c.url}"
    return line


def proposal_text(proposal: Proposal) -> str:
    items = "\n".join(f"• {i.name}: {i.quantity} {i.unit} × {money(i.unit_price)} ₸ = {money(i.line_total)} ₸"
                      for i in proposal.items)
    return (f"Готов добавить в корзину:\n{items}\nИтого: {money(proposal.total)} ₸.\n"
            f"Подтвердите кнопкой «Добавить в корзину» или напишите «да, добавь». Пока вы не подтвердите, "
            f"корзина не изменится.")


def applied_text(proposal: Proposal, cart_url: str) -> str:
    items = ", ".join(f"{i.name} × {i.quantity} {i.unit}" for i in proposal.items)
    return f"Добавил в корзину: {items}. Корзина и оформление заказа: {cart_url}"
