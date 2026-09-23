"""Правила проверки данных каталога.

product_from_source() превращает сырую карточку EKT (или синтетическую в том же
формате) в Product. Здесь собраны все решения о качестве данных: какие склады
считаются продающими, как читаются характеристики и что делать с противоречиями.
"""

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit

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
    ("cable_type", "Марка кабеля", None, ("MARKA_KABELYA",), ()),
    ("conductor_material", "Материал жилы", None, ("MATERIAL_ZHILY",), ()),
    ("insulation", "Материал изоляции", None, ("MATERIAL_IZOLYATSII",), ()),
    ("fire_class", "Класс пожарной безопасности", None, ("KLASS_POZHARNOY_BEZOPASNOSTI",), ()),
    ("trip_unit", "Тип расцепителя", None, ("TIP_RASTSEPITELYA",), ()),
    ("residual_type", "Тип дифференциальной защиты", None, ("TIP_DIFFERENTSIALNOY_ZASHCHITY",), ()),
]

# Параметры, расхождение в которых делает товар непригодным для автоматического подбора.
CRITICAL_ATTRIBUTES = {"device_type", "rated_current", "poles", "voltage", "breaking_capacity",
                       "leakage_current", "curve", "cross_section", "cable_type", "conductor_material",
                       "insulation", "fire_class", "trip_unit", "residual_type"}

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


UNIT_SCALE = {
    "а": ("current", Decimal(1)), "a": ("current", Decimal(1)),
    "ма": ("current", Decimal("0.001")), "ma": ("current", Decimal("0.001")),
    "ка": ("current", Decimal(1000)), "ka": ("current", Decimal(1000)),
    "в": ("voltage", Decimal(1)), "v": ("voltage", Decimal(1)),
    "кв": ("voltage", Decimal(1000)), "kv": ("voltage", Decimal(1000)),
    "вт": ("power", Decimal(1)), "w": ("power", Decimal(1)),
    "квт": ("power", Decimal(1000)), "kw": ("power", Decimal(1000)),
    "лм": ("flux", Decimal(1)), "lm": ("flux", Decimal(1)),
    "к": ("temperature", Decimal(1)), "k": ("temperature", Decimal(1)),
}


def scalar_value(value: str, unit: str | None = None) -> Decimal | None:
    """Only a complete scalar in a compatible known unit is comparable numerically."""
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*([A-Za-zА-Яа-я]*)\s*", value)
    if not match:
        return None
    amount = Decimal(match[1].replace(",", "."))
    suffix, expected = match[2].lower(), (unit or "").lower()
    if not suffix:
        return amount
    actual_unit, expected_unit = UNIT_SCALE.get(suffix), UNIT_SCALE.get(expected)
    if actual_unit is None or (expected and (expected_unit is None or actual_unit[0] != expected_unit[0])):
        return None
    return amount * actual_unit[1] / (expected_unit[1] if expected_unit else Decimal(1))


def canonical(value: str, unit: str | None = None) -> str:
    """Preserve complete compound values, ranges and model names; normalize scalars."""
    scalar = scalar_value(value, unit)
    if scalar is not None:
        return format(scalar.normalize(), "f")
    text = re.sub(r"\s+", "", value.strip().lower())
    text = re.sub(r"(?<=\d)[х×*](?=\d)", "x", text)
    if re.fullmatch(r"\d+(?:[.,]\d+)?(?:[x/]\d+(?:[.,]\d+)?)+", text):
        return NUMBER.sub(lambda m: format(Decimal(m[0].replace(",", ".")).normalize(), "f"), text)
    return text


def _name_observations(name: str) -> dict[str, str]:
    found = {}
    if m := re.search(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*А\b", name):
        found["rated_current"] = m.group(1)
    if m := re.search(r"\b([1-4])P(\+N)?", name):
        # 1P+N это два полюса (фаза и ноль)
        found["poles"] = str(int(m.group(1)) + (1 if m.group(2) else 0))
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
        if key in from_name:
            observations.append(Observation("name", from_name[key]))
        if not observations:
            continue
        distinct = {canonical(o.value, unit) for o in observations}
        if len(distinct) > 1:
            status, value = AttributeStatus.CONFLICT, None
            warnings.append(f"conflict:{key}")
        else:
            status, value = AttributeStatus.OK, observations[0].value
        attributes.append(Attribute(key, label, value, unit, status, tuple(observations)))
    return attributes, warnings


def category_from_url(url: str) -> tuple[str, ...]:
    """Путь категории из url карточки, без последнего сегмента (slug товара)."""
    if not safe_url(url):
        return ()
    path = urlsplit(url).path
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
    return price if price.is_finite() and price > 0 else None


def safe_url(value) -> str | None:
    """Allow ordinary HTTP(S) and same-origin paths; reject executable URLs."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    decoded = unquote(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            return None
        if parsed.scheme:
            return value if parsed.scheme.lower() in {"http", "https"} and parsed.hostname else None
        return value if decoded.startswith("/") and not decoded.startswith("//") and not parsed.netloc else None
    except ValueError:
        return None


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
    certificates = []
    for certificate in raw.get("certificates") or []:
        url = safe_url(certificate.get("url"))
        if url:
            certificates.append(Certificate(certificate.get("type") or "Сертификат", certificate.get("number"),
                                            certificate.get("valid_until"), url))
        else:
            warnings.append("unsafe_certificate_url")
    url, image = safe_url(raw.get("url")), safe_url(raw.get("image"))
    if raw.get("url") and not url:
        warnings.append("unsafe_product_url")
    if raw.get("image") and not image:
        warnings.append("unsafe_image_url")
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
        url=url or "",
        image=image,
        description=raw.get("description") or "",
        attributes=tuple(attributes),
        stock=sellable_stock(raw.get("stores"), int(reported) if isinstance(reported, (int, float)) else None),
        certificates=tuple(certificates),
        fetched_at=fetched_at or datetime.now(timezone.utc),
        warnings=tuple(warnings),
    )
