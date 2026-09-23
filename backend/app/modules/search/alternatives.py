"""Подбор аналогов с объяснением.

Правило простое и проверяемое: аналог из той же категории, в наличии, и все
ключевые параметры исходного товара совпадают. Если у исходного товара ключевой
параметр спорный, автоматический подбор по нему не делаем.
"""

from dataclasses import dataclass

from app.modules.catalog.models import AttributeStatus, Product, StockStatus
from app.modules.catalog.ports import CatalogReader
from app.modules.catalog.quality import canonical

# Что обязано совпасть, чтобы товар был заменой. Порядок важен для текста обоснования.
MATCH_KEYS = ["device_type", "rated_current", "poles", "leakage_current", "curve", "voltage",
              "power", "color_temperature", "cross_section"]
# Совпадение этих параметров не обязательно, но повышает место в списке.
BONUS_KEYS = ["breaking_capacity", "series", "color", "mounting", "ip", "luminous_flux"]


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


def _value(product: Product, key: str) -> str | None:
    attr = product.attribute(key)
    return attr.value if attr and attr.status is AttributeStatus.OK else None


class AlternativeService:
    def __init__(self, catalog: CatalogReader):
        self.catalog = catalog

    def find(self, product: Product, limit: int = 3) -> AlternativesResult:
        blocked = tuple(
            a.label for a in product.attributes if a.key in MATCH_KEYS and a.status is AttributeStatus.CONFLICT
        )
        if blocked or not product.category_path:
            return AlternativesResult(product, (), blocked)

        required = [(k, _value(product, k)) for k in MATCH_KEYS if _value(product, k)]
        candidates = []
        for other in self.catalog.list_category(product.category_path):
            if other.id == product.id or other.stock.status is not StockStatus.IN_STOCK:
                continue
            if other.unit != product.unit:
                continue
            matched, ok = [], True
            for key, value in required:
                other_value = _value(other, key)
                if other_value is None or canonical(other_value) != canonical(value):
                    ok = False
                    break
                matched.append(f"{other.attribute(key).label}: {other_value}")
            if not ok:
                continue
            differences, bonus = [], 0
            for key in BONUS_KEYS:
                mine, theirs = _value(product, key), _value(other, key)
                if mine and theirs:
                    if canonical(mine) == canonical(theirs):
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
            result.append(Alternative(other, tuple(matched), tuple(differences), reason))
        return AlternativesResult(product, tuple(result), ())
