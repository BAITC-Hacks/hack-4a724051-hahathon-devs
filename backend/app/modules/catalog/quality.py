"""Правила проверки данных каталога.

product_from_source() превращает сырую карточку EKT (или синтетическую в том же
формате) в Product. Здесь собраны все решения о качестве данных: какие склады
считаются продающими, как читаются характеристики и что делать с противоречиями.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.modules.catalog.models import (
    Attribute, AttributeStatus, Certificate, Observation, Product, Stock, StockStatus, StoreStock,
)

# Склады, остаток на которых нельзя предлагать покупателю.
NON_SALE_STORES = {
    "брак megalight", "маркетинг megalight", "востановленный продукт", "перемещение",
    "образцы отдел закупа", "витрина тз",
}

# key, подпись, единица, ключи properties, подписи в описании
ATTRIBUTE_RULES = [
    ("rated_current", "Номинальный ток", "А", ("NOMINALNYY_TOK", "NOMINALNYY_TOK_A_1"), ("номинальный ток",)),
    ("poles", "Количество полюсов", None, ("KOLICHESTVO_POLYUSOV",), ("количество полюсов",)),
    ("voltage", "Номинальное напряжение", "В", ("NOMINALNOE_NAPRYAZHENIE",), ("номинальное напряжение", "напряжение")),
    ("breaking_capacity", "Отключающая способность", "кА",
     ("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",), ("отключающая способность",)),
    ("curve", "Характеристика срабатывания", None, ("KHARAKTERISTIKA_SRABATYVANIYA",),
     ("характеристика срабатывания", "характеристика")),
    ("leakage_current", "Ток утечки", "мА", ("NOMINALNYY_OTKLYUCHAYUSHCHIY_DIFFERENTSIALNYY_TOK",), ("ток утечки",)),
    ("series", "Серия", None, ("SERIYA", "SERIYA_1"), ("серия",)),
    ("device_type", "Тип устройства", None, ("TIP_USTROYSTVA",), ()),
    ("power", "Мощность", "Вт", ("MOSHCHNOST", "MOSHCHNOST_W"), ("мощность",)),
    ("luminous_flux", "Световой поток", "Лм", ("SVETOVOY_POTOK_LM",), ("световой поток",)),
    ("color_temperature", "Цветовая температура", "K", ("TSVETOVAYA_TEMPERATURA",), ()),
    ("ip", "Степень защиты", None, ("STEPEN_ZASHCHITY",), ()),
    ("color", "Цвет", None, ("TSVET", "TSVET_1"), ("цвет",)),
    ("cross_section", "Сечение", None, ("SECHENIE",), ()),
    ("mounting", "Тип установки", None, ("TIP_USTANOVKI",), ()),
]

# Параметры, расхождение в которых делает товар непригодным для автоматического подбора.
CRITICAL_ATTRIBUTES = {"rated_current", "poles", "voltage", "breaking_capacity", "leakage_current", "curve"}

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
# Значения, где несколько чисел это норма: сечение 3х2,5, серия ВА47-29.
MULTI_NUMBER_KEYS = {"cross_section", "series", "device_type", "color_temperature", "ip"}


def canonical(value: str) -> str:
    """Сравнимая форма значения: все числа без единиц, либо текст в нижнем регистре.

    Берём все числа, а не первое: у кабеля 3х2,5 и 3х4 первое число одинаковое.
    """
    numbers = NUMBER.findall(value)
    if numbers:
        return "x".join(format(Decimal(n.replace(",", ".")).normalize(), "f") for n in numbers)
    return value.strip().lower()


def _name_observations(name: str) -> dict[str, str]:
    found = {}
    if m := re.search(r"(\d+)\s*А\b", name):
        found["rated_current"] = m.group(1)
    # «1P+N» в каталоге записан то как 1, то как 2 полюса, поэтому из такого названия полюса не берём.
    if m := re.search(r"\b([1-4])P\b(?!\s*\+\s*N)", name):
        found["poles"] = m.group(1)
    return found


def _description_observations(description: str) -> dict[str, str]:
    found = {}
    for line in description.replace("\r", "").split("\n"):
        if ":" not in line:
            continue
        label, value = (part.strip() for part in line.split(":", 1))
        label = label.lower()
        for key, _, _, _, labels in ATTRIBUTE_RULES:
            if key not in found and label in labels and value:
                # «16, 25, 40 А» это перечень значений серии, а не значение этого товара.
                if key not in MULTI_NUMBER_KEYS and len(NUMBER.findall(value)) > 1:
                    continue
                found[key] = value
    return found


def extract_attributes(name: str, description: str, properties: dict) -> tuple[list[Attribute], list[str]]:
    from_name = _name_observations(name)
    from_description = _description_observations(description)
    attributes, warnings = [], []
    for key, label, unit, prop_keys, _ in ATTRIBUTE_RULES:
        observations = []
        if key in from_description:
            observations.append(Observation("description", from_description[key]))
        for pk in prop_keys:
            value = properties.get(pk)
            if isinstance(value, str) and value.strip():
                observations.append(Observation(f"properties.{pk}", value.strip()))
                break
        if key in from_name:
            observations.append(Observation("name", from_name[key]))
        if not observations:
            continue
        distinct = {canonical(o.value) for o in observations}
        if len(distinct) > 1:
            status, value = AttributeStatus.CONFLICT, None
            warnings.append(f"conflict:{key}")
        else:
            status, value = AttributeStatus.OK, observations[0].value
        attributes.append(Attribute(key, label, value, unit, status, tuple(observations)))
    return attributes, warnings


def category_from_url(url: str) -> tuple[str, ...]:
    """Путь категории из url карточки, без последнего сегмента (slug товара)."""
    path = re.sub(r"^https?://[^/]+", "", url or "")
    if "/catalog/" not in path:
        return ()
    parts = [p for p in path.split("/catalog/", 1)[1].split("/") if p]
    return tuple(parts[:-1])


def sellable_stock(stores: list[dict] | None, reported: int | None) -> Stock:
    if stores is None:
        status = StockStatus.UNKNOWN if reported is None else (
            StockStatus.IN_STOCK if reported > 0 else StockStatus.OUT_OF_STOCK)
        return Stock(status, reported, None)
    parsed = tuple(StoreStock(int(s["id"]), str(s["name"]), int(s.get("quantity") or 0)) for s in stores)
    sellable = sum(s.quantity for s in parsed if s.name.strip().lower() not in NON_SALE_STORES and s.quantity > 0)
    total = sum(s.quantity for s in parsed if s.quantity > 0)
    if sellable > 0:
        status = StockStatus.IN_STOCK
    elif total > 0:
        status = StockStatus.NOT_SELLABLE
    else:
        status = StockStatus.OUT_OF_STOCK
    return Stock(status, reported, sellable, parsed)


def _price(value) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
    return price if price > 0 else None


def product_from_source(raw: dict, fetched_at: datetime | None = None) -> Product:
    """Сырая карточка /api/products/detail -> Product. Ничего не додумывает."""
    properties = raw.get("properties") or {}
    attributes, warnings = extract_attributes(raw.get("name", ""), raw.get("description") or "", properties)
    price = _price(raw.get("price"))
    if price is None:
        warnings.append("price_missing")
    try:
        min_order = int(properties["KRATNOST_MIN"]) if properties.get("KRATNOST_MIN") else None
    except ValueError:
        min_order = None
    category_path = category_from_url(raw.get("url", ""))
    if not category_path:
        warnings.append("category_unknown")
    certificates = tuple(
        Certificate(c.get("type") or "Сертификат", c.get("number"), c.get("valid_until"), c["url"])
        for c in raw.get("certificates") or [] if c.get("url")
    )
    reported = raw.get("quantity")
    return Product(
        id=int(raw["id"]),
        article=str(raw.get("article") or ""),
        supplier_article=properties.get("ARTIKULPOSTAVSHCHIKA"),
        name=str(raw.get("name") or ""),
        brand=properties.get("TORGOVAYA_MARKA"),
        price=price,
        unit=properties.get("EDINITSA_IZMERENIYA") or "шт",
        min_order=min_order,
        category_path=category_path,
        url=str(raw.get("url") or ""),
        image=raw.get("image"),
        description=raw.get("description") or "",
        attributes=tuple(attributes),
        stock=sellable_stock(raw.get("stores"), int(reported) if isinstance(reported, (int, float)) else None),
        certificates=certificates,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        warnings=tuple(warnings),
        barcode=properties.get("CML2_BAR_CODE") or None,
    )
