"""Инструменты, которые видит модель.

Все инструменты читают данные, кроме propose_cart, который создаёт предложение,
но корзину не меняет. Подтверждения и записи в корзину у модели нет.
Каждый результат инструмента запоминается в Facts, по ним потом проверяется ответ.
"""

import json
from dataclasses import dataclass, field

from app.modules.actions.models import Proposal
from app.modules.actions.service import ActionService
from app.modules.assistant.ports import ToolCall, ToolResult, ToolSpec
from app.modules.catalog.models import AttributeStatus, Product, StockStatus
from app.modules.catalog.ports import CatalogReader
from app.modules.catalog.quality import NON_SALE_STORES
from app.modules.knowledge.service import PurchaseTermsService
from app.modules.search.alternatives import AlternativeService
from app.modules.search.service import SearchService

DESCRIPTION_LIMIT = 700


@dataclass
class Facts:
    """Всё, что ассистент узнал за ход из проверенных источников."""
    products: dict[int, Product] = field(default_factory=dict)
    shown: list[int] = field(default_factory=list)  # порядок карточек для фронтенда
    proposal: Proposal | None = None
    sources: list[str] = field(default_factory=list)
    terms_text: list[str] = field(default_factory=list)

    def remember(self, product: Product, show: bool = True) -> None:
        self.products[product.id] = product
        if show and product.id not in self.shown:
            self.shown.append(product.id)


def _stock_view(p: Product) -> dict:
    by_city = {s.name: s.quantity for s in p.stock.stores
               if s.quantity > 0 and s.name.strip().lower() not in NON_SALE_STORES}
    note = {
        StockStatus.IN_STOCK: "в наличии",
        StockStatus.OUT_OF_STOCK: "нет в наличии",
        StockStatus.NOT_SELLABLE: "нет в наличии для продажи (остаток только на служебных складах)",
        StockStatus.UNKNOWN: "остаток не удалось проверить, не говори что товара нет",
    }[p.stock.status]
    return {"status": p.stock.status.value, "note": note, "sellable_quantity": p.stock.sellable_quantity,
            "by_store": by_city}


def product_brief(p: Product) -> dict:
    return {
        "id": p.id, "name": p.name, "article": p.article, "brand": p.brand,
        "price_kzt": str(p.price) if p.price is not None else None, "unit": p.unit,
        "stock": _stock_view(p), "category": p.category,
    }


def product_full(p: Product) -> dict:
    attrs = {}
    for a in p.attributes:
        if a.status is AttributeStatus.CONFLICT:
            attrs[a.label] = "ПРОТИВОРЕЧИЕ В ДАННЫХ: " + "; ".join(f"{o.source}={o.value}" for o in a.observations)
        else:
            attrs[a.label] = a.value
    return {
        **product_brief(p),
        "supplier_article": p.supplier_article,
        "min_order": p.min_order,
        "url": p.url,
        "attributes": attrs,
        "certificates": [
            {"title": c.title, "number": c.number, "valid_until": c.valid_until, "url": c.url}
            for c in p.certificates
        ] or "сертификата в базе нет",
        "description_untrusted": p.description[:DESCRIPTION_LIMIT],
        "warnings": list(p.warnings),
    }


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


class ToolBox:
    def __init__(self, catalog: CatalogReader, search: SearchService, alternatives: AlternativeService,
                 terms: PurchaseTermsService, actions: ActionService):
        self.catalog = catalog
        self.search = search
        self.alternatives = alternatives
        self.terms = terms
        self.actions = actions

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "search_products",
                "Найти товары в каталоге ekt.kz по артикулу, коду производителя или описанию "
                "(например «автомат 16А 1P IEK»). Возвращает до 8 товаров с ценой и наличием.",
                _schema({"query": {"type": "string", "description": "Запрос, артикул или название"}}, ["query"]),
            ),
            ToolSpec(
                "get_product",
                "Полная карточка товара: характеристики, наличие по складам, сертификаты, ссылка. "
                "Вызывай перед тем как называть характеристики или сертификат.",
                _schema({"product_id": {"type": "integer"}}, ["product_id"]),
            ),
            ToolSpec(
                "find_alternatives",
                "Подобрать аналоги товара, которые есть в наличии. Возвращает до 3 товаров и "
                "обоснование: какие параметры совпадают и чем отличаются.",
                _schema({"product_id": {"type": "integer"}}, ["product_id"]),
            ),
            ToolSpec(
                "get_purchase_terms",
                "Условия покупки ekt.kz из утверждённого текста: оплата, доставка, самовывоз, минимальная "
                "партия, рассрочка, возврат, покупка на юрлицо, цены на сайте.",
                _schema({"topics": {"type": "array", "items": {"type": "string", "enum": self.terms.topic_ids()},
                                    "minItems": 1}}, ["topics"]),
            ),
            ToolSpec(
                "propose_cart",
                "Подготовить предложение добавить товары в корзину. Корзину НЕ меняет: клиент увидит состав "
                "и сам подтвердит кнопкой или словами «да, добавь». Вызывай, когда клиент хочет купить и "
                "понятны товар и количество.",
                _schema({"items": {"type": "array", "minItems": 1, "maxItems": 20, "items": _schema(
                    {"product_id": {"type": "integer"}, "quantity": {"type": "integer", "minimum": 1}},
                    ["product_id", "quantity"])}}, ["items"]),
            ),
        ]

    def execute(self, call: ToolCall, session_id: str, facts: Facts) -> ToolResult:
        handler = {
            "search_products": self._search, "get_product": self._get_product,
            "find_alternatives": self._alternatives, "get_purchase_terms": self._terms,
            "propose_cart": self._propose,
        }.get(call.name)
        if handler is None:
            return self._error(call, "unknown_tool")
        try:
            payload = handler(call.input, session_id, facts)
        except (KeyError, TypeError, ValueError):
            return self._error(call, "bad_arguments")
        return ToolResult(call.id, json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _error(call: ToolCall, code: str) -> ToolResult:
        return ToolResult(call.id, json.dumps({"error": code}), is_error=True)

    @staticmethod
    def _int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError
        return value

    def _search(self, args, session_id, facts):
        matches = self.search.search(str(args["query"]))
        for m in matches:
            facts.remember(m.product, show=False)
        return {"results": [{**product_brief(m.product), "match": m.kind.value} for m in matches],
                "note": "Каталог может быть неполным: если ничего не найдено, не утверждай что товара нет в магазине."
                if not matches else None}

    def _get_product(self, args, session_id, facts):
        product = self.catalog.get_product(self._int(args["product_id"]))
        if product is None:
            return {"error": "not_found"}
        facts.remember(product)
        return product_full(product)

    def _alternatives(self, args, session_id, facts):
        product = self.catalog.get_product(self._int(args["product_id"]))
        if product is None:
            return {"error": "not_found"}
        facts.remember(product)
        result = self.alternatives.find(product)
        for alt in result.alternatives:
            facts.remember(alt.product)
        if result.not_matched_reason == "no_attributes":
            return {"alternatives": [], "reason": "У товара в каталоге нет характеристик, по которым можно доказать "
                    "замену. Уточни у клиента нужные параметры или предложи связаться с менеджером."}
        if result.blocked_by:
            return {"alternatives": [], "reason": "У исходного товара противоречивые данные: "
                    + ", ".join(result.blocked_by) + ". Автоматический подбор по ним небезопасен, уточни у клиента "
                    "нужное значение или предложи связаться с менеджером."}
        return {"alternatives": [{**product_brief(a.product), "why": a.reason, "matched": list(a.matched),
                                  "differences": list(a.differences)} for a in result.alternatives]}

    def _terms(self, args, session_id, facts):
        topics = self.terms.get([str(t) for t in args["topics"]])
        for t in topics:
            facts.terms_text.append(t.text)
            facts.sources.extend(s for s in t.sources if s not in facts.sources)
        return {"topics": [{"title": t.title, "text": t.text, "caveats": list(t.caveats), "sources": list(t.sources)}
                           for t in topics]}

    def _propose(self, args, session_id, facts):
        items = [(self._int(i["product_id"]), i["quantity"]) for i in args["items"]]
        result = self.actions.prepare_proposal(session_id, items)
        if not result.ok:
            return {"created": False, "problems": [p.message for p in result.problems] or [result.message]}
        facts.proposal = result.proposal
        for item in result.proposal.items:
            if product := self.catalog.get_product(item.product_id):
                facts.remember(product)
        return {
            "created": True, "cart_changed": False,
            "items": [{"name": i.name, "quantity": i.quantity, "unit": i.unit, "unit_price": str(i.unit_price)}
                      for i in result.proposal.items],
            "total_kzt": str(result.proposal.total),
            "next_step": "Попроси клиента подтвердить: кнопка «Добавить в корзину» или ответ «да, добавь».",
        }
