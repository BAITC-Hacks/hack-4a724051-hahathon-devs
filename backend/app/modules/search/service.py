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
    SEMANTIC = "semantic"  # похож по смыслу (эмбеддинги), без совпадения слов


@dataclass(frozen=True)
class SearchMatch:
    product: Product
    kind: MatchKind


# Похоже на артикул: цифры/латиница, возможно с "_" или "-", без пробелов.
IDENTIFIER = re.compile(r"^[0-9A-Za-zА-Яа-я][0-9A-Za-zА-Яа-я_\-./]{3,}$")


def identifier_candidates(query: str) -> list[str]:
    # Таблицы из файлов и распознанных фото приходят как «1|990100003_|Автомат|6 шт».
    tokens = [t.strip(" ,;:\"'«»()") for t in re.split(r"[\s|;,]+", query)]
    return [t for t in tokens if IDENTIFIER.match(t) and any(c.isdigit() for c in t)]


RRF_K = 60  # константа reciprocal rank fusion: сглаживает разницу между первыми местами двух списков

# Как клиент пишет -> как записано в каталоге.
QUERY_NORMALIZATION = [
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ампер\w*|amp\w*|а\b)", re.I), r"\1А"),
    # Распознанный текст часто пишет «1Р», «1Р+N» кириллицей: в каталоге латиница.
    (re.compile(r"\b(\d)[РрP](?=\+|\b)"), r"\1P"),
    (re.compile(r"(?<=\d[AА] )С\b"), "C"),
    (re.compile(r"\bоднополюсн\w*", re.I), "1P"),
    (re.compile(r"\bдвухполюсн\w*", re.I), "2P"),
    (re.compile(r"\bтр[её]хполюсн\w*", re.I), "3P"),
    (re.compile(r"\bчетыр[её]хполюсн\w*", re.I), "4P"),
    # Разговорные названия: слово клиента остаётся, добавляется название из каталога.
    (re.compile(r"(?<!диф)(?<!диф\.)\bавтомат(ы|а|ов)?\b", re.I), "автомат автоматический выключатель"),
    (re.compile(r"\bдифавтомат\w*", re.I), "дифференциальный автомат"),
    (re.compile(r"\bузо\b", re.I), "УЗО дифференциальный"),
]
STOPWORDS = {"для", "что", "как", "это", "под", "над", "при", "или", "где", "куда", "надо", "нужен", "нужна",
             "нужно", "есть", "мне", "вам", "нам", "какой", "какая", "какие", "можно", "по", "на", "в", "с",
             "и", "а", "из", "от", "до", "за", "the", "for", "and", "with"}


def normalize_query(query: str) -> str:
    for pattern, replacement in QUERY_NORMALIZATION:
        query = pattern.sub(replacement, query)
    return query


def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2 and t not in STOPWORDS}


class SearchService:
    def __init__(self, catalog: CatalogReader, semantic=None):
        self.catalog = catalog
        self.semantic = semantic  # SemanticIndex или None

    def search(self, query: str, limit: int = 8) -> list[SearchMatch]:
        query = normalize_query(query.strip()[:200])
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

        if any(m.kind is MatchKind.EXACT for m in seen.values()):
            order = {MatchKind.EXACT: 0, MatchKind.NORMALIZED: 1}
            return sorted(seen.values(), key=lambda m: order[m.kind])[:limit]

        found = [m for m in seen.values()]
        # Совпадение только по служебным словам («для», «по») это шум, а не находка.
        wanted = _meaningful_tokens(query)
        lexical = [p for p in self.catalog.search_text(query, limit=limit) if wanted & _meaningful_tokens(p.name)]
        semantic = self.semantic.search(query, limit=limit) if self.semantic is not None else []
        if not semantic:
            for product in lexical:
                if product.id not in seen:
                    found.append(SearchMatch(product, MatchKind.TEXT))
            return found[:limit]

        # Слияние двух выдач по местам (RRF): товар высоко в обеих поднимается выше всех.
        scores: dict[int, float] = {}
        for rank, product in enumerate(lexical):
            scores[product.id] = scores.get(product.id, 0) + 1 / (RRF_K + rank)
        for rank, (pid, _) in enumerate(semantic):
            scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank)
        by_id = {p.id: p for p in lexical}
        lexical_ids = set(by_id)
        for pid, _ in sorted(scores.items(), key=lambda s: (-s[1], s[0])):
            if pid in seen:
                continue
            product = by_id.get(pid) or self.catalog.get_product(pid)
            if product is not None:
                found.append(SearchMatch(product, MatchKind.TEXT if pid in lexical_ids else MatchKind.SEMANTIC))
        return found[:limit]
