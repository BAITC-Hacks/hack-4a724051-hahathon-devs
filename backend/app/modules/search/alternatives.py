"""Подбор аналогов с объяснением.

Кандидаты сравниваются только по явно заданным профилям категории и типа.
Участник 1 поставляет проверенные данные каталога и реализацию CatalogReader.
Пропуски и противоречия в обязательных параметрах блокируют автоматический подбор.
Результат — кандидат для проверки, а не гарантия инженерной взаимозаменяемости.
"""

from dataclasses import dataclass
import re

from app.modules.catalog.models import AttributeStatus, Product, StockStatus
from app.modules.catalog.ports import CatalogReader
from app.modules.catalog.quality import CRITICAL_ATTRIBUTES, canonical, scalar_value

# Что обязано совпасть, чтобы товар был заменой. Порядок важен для текста обоснования.
MATCH_KEYS = ["device_type", "rated_current", "poles", "leakage_current", "curve", "voltage",
              "power", "color_temperature", "cross_section"]
# Совпадение этих параметров не обязательно, но повышает место в списке.
BONUS_KEYS = ["series", "color", "mounting", "ip", "luminous_flux"]

# Unknown categories deliberately have no generic "same category" fallback.
# Cable construction must be supplied as structured facts, never guessed from a name.
PROFILES = {
    "modulnye_avtomaticheskie_vyklyuchateli": (
        "автоматический выключатель", ("device_type", "rated_current", "poles", "voltage", "curve", "breaking_capacity")),
    "silovye_avtomaticheskie_vyklyuchateli": (
        "автоматический выключатель в литом корпусе", ("device_type", "rated_current", "poles", "voltage", "breaking_capacity", "trip_unit")),
    "differentsialnye_avtomaty": (
        "дифференциальный автоматический выключатель",
        ("device_type", "rated_current", "poles", "voltage", "curve", "breaking_capacity", "leakage_current", "residual_type")),
    "kabel_silovoy": (None, ("cross_section", "voltage", "cable_type", "conductor_material", "insulation", "fire_class")),
}
NUMERIC_UNITS = {"rated_current": "А", "poles": None, "voltage": "В", "breaking_capacity": "кА", "leakage_current": "мА"}


@dataclass(frozen=True)
class Alternative:
    product: Product
    matched: tuple[str, ...]  # "Номинальный ток: 16А"
    differences: tuple[str, ...]  # "Отключающая способность: 6кА вместо 4,5кА"
    reason: str


@dataclass(frozen=True)
class AlternativesResult:
    source: Product
    alternatives: tuple[Alternative, ...]
    blocked_by: tuple[str, ...]  # спорные параметры исходного товара
    # conflict | no_attributes | no_category, если подбор не делался
    not_matched_reason: str | None = None


def _value(product: Product, key: str) -> str | None:
    attr = product.attribute(key)
    return attr.value if attr and attr.status is AttributeStatus.OK else None


def _verified(value: str | None, key: str) -> bool:
    if not value or value.strip().lower() in {"unknown", "неизвестно", "уточняется", "n/a", "-", "нет данных"}:
        return False
    if key == "poles":
        return bool(re.fullmatch(r"[1-4]", value.strip()))
    if key == "curve":
        return value.strip().upper() in {"B", "C", "D", "K", "Z"}
    if key == "cross_section":
        return bool(re.fullmatch(r"[1-9]\d*x(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)", canonical(value)))
    if key in NUMERIC_UNITS:
        number = scalar_value(value, NUMERIC_UNITS[key])
        return number is not None and number > 0
    return True


class AlternativeService:
    def __init__(self, catalog: CatalogReader):
        self.catalog = catalog

    def find(self, product: Product, limit: int = 3) -> AlternativesResult:
        profile = next((PROFILES[part] for part in reversed(product.category_path) if part in PROFILES), None)
        blocked = [a.label for a in product.attributes
                   if a.key in CRITICAL_ATTRIBUTES and a.status is AttributeStatus.CONFLICT]
        if blocked:
            return AlternativesResult(product, (), tuple(blocked), "conflict")
        if not product.category_path:
            return AlternativesResult(product, (), ("category_profile_unknown",), "no_category")
        if not any(_value(product, key) for key in MATCH_KEYS):
            return AlternativesResult(product, (), ("required_attributes_unknown",), "no_attributes")
        if profile is None:
            return AlternativesResult(product, (), ("category_profile_unknown",), "unsupported_category")
        expected_type, profile_keys = profile
        if expected_type and (_value(product, "device_type") or "").strip().casefold() != expected_type:
            blocked.append("device_type_unverified")
        for key in profile_keys:
            value = _value(product, key)
            if value is None or not value.strip():
                blocked.append(f"missing:{key}")
            elif not _verified(value, key):
                blocked.append(f"unverified:{key}")
        if blocked:
            return AlternativesResult(product, (), tuple(dict.fromkeys(blocked)), "no_attributes")

        required_keys = dict.fromkeys([*profile_keys, *(k for k in MATCH_KEYS if _value(product, k))])
        required = [(k, _value(product, k)) for k in required_keys]
        candidates = []
        for other in self.catalog.list_category(product.category_path):
            if (other.id == product.id or other.stock.status is not StockStatus.IN_STOCK
                    or not other.stock.sellable_quantity or other.stock.sellable_quantity <= 0):
                continue
            if other.unit != product.unit:
                continue
            if any(a.key in CRITICAL_ATTRIBUTES and a.status is AttributeStatus.CONFLICT for a in other.attributes):
                continue
            matched, differences, ok = [], [], True
            for key, value in required:
                other_value = _value(other, key)
                if not _verified(other_value, key):
                    ok = False
                    break
                unit = product.attribute(key).unit
                if key == "breaking_capacity":
                    mine, theirs = scalar_value(value, "кА"), scalar_value(other_value, "кА")
                    if mine is None or theirs is None or theirs < mine:
                        ok = False
                        break
                    if theirs > mine:
                        differences.append(f"{other.attribute(key).label}: {other_value} вместо {value}")
                        continue
                elif canonical(other_value, unit) != canonical(value, unit):
                    ok = False
                    break
                matched.append(f"{other.attribute(key).label}: {other_value}")
            if not ok:
                continue
            bonus = 0
            for key in BONUS_KEYS:
                mine, theirs = _value(product, key), _value(other, key)
                if mine and theirs:
                    if canonical(mine, product.attribute(key).unit) == canonical(theirs, product.attribute(key).unit):
                        bonus += 1
                    else:
                        differences.append(f"{other.attribute(key).label}: {theirs} вместо {mine}")
            if other.brand and product.brand and other.brand != product.brand:
                differences.append(f"Производитель: {other.brand} вместо {product.brand}")
            candidates.append((bonus, -len(differences), other, matched, differences))

        candidates.sort(key=lambda c: (c[0], c[1], c[2].stock.sellable_quantity or 0), reverse=True)
        result = []
        for _, _, other, matched, differences in candidates[:limit]:
            reason = "Совпадают " + ", ".join(m.split(":")[0].lower() for m in matched) if matched \
                else "Та же категория"
            reason += f"; в наличии {other.stock.sellable_quantity} {other.unit}"
            if differences:
                reason += "; отличия: " + "; ".join(differences)
            reason += "; кандидат по данным каталога, совместимость требует проверки"
            result.append(Alternative(other, tuple(matched), tuple(differences), reason))
        return AlternativesResult(product, tuple(result), ())
