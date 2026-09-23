"""Поиск товара по запросу клиента. Работает без LLM."""

import re
from dataclasses import dataclass
from enum import Enum

from app.modules.catalog.models import Product
from app.modules.catalog.ports import CatalogReader


class MatchKind(str, Enum):
    EXACT = "exact"  # совпал артикул или ID как есть
    NORMALIZED = "normalized"  # совпал после удаления разделителей, это кандидат
    TEXT = "text"


@dataclass(frozen=True)
class SearchMatch:
    product: Product
    kind: MatchKind


# Похоже на артикул: цифры/латиница, возможно с "_" или "-", без пробелов.
IDENTIFIER = re.compile(r"^[0-9A-Za-zА-Яа-я][0-9A-Za-zА-Яа-я_\-./]{3,}$")


def identifier_candidates(query: str) -> list[str]:
    tokens = [t.strip(" ,;:\"'«»()") for t in query.split()]
    return [t for t in tokens if IDENTIFIER.match(t) and any(c.isdigit() for c in t)]


class SearchService:
    def __init__(self, catalog: CatalogReader):
        self.catalog = catalog

    def search(self, query: str, limit: int = 8) -> list[SearchMatch]:
        query = query.strip()[:200]
        if not query:
            return []
        seen: dict[int, SearchMatch] = {}

        if query.isdigit():
            product = self.catalog.get_product(int(query))
            if product:
                seen[product.id] = SearchMatch(product, MatchKind.EXACT)

        for token in identifier_candidates(query):
            for product in self.catalog.find_by_identifier(token):
                seen.setdefault(product.id, SearchMatch(product, MatchKind.EXACT))
            stripped = token.strip("_-")
            if stripped != token:
                for product in self.catalog.find_by_identifier(stripped):
                    seen.setdefault(product.id, SearchMatch(product, MatchKind.NORMALIZED))

        if not any(m.kind is MatchKind.EXACT for m in seen.values()):
            for product in self.catalog.search_text(query, limit=limit):
                seen.setdefault(product.id, SearchMatch(product, MatchKind.TEXT))

        order = {MatchKind.EXACT: 0, MatchKind.NORMALIZED: 1, MatchKind.TEXT: 2}
        return sorted(seen.values(), key=lambda m: order[m.kind])[:limit]
