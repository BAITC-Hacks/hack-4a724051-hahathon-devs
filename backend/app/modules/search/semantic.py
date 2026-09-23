"""Смысловой поиск по каталогу на эмбеддингах NVIDIA.

Векторы карточек считаются заранее (python -m app.catalog_cli embed) и лежат в файле,
на запрос клиента уходит один вызов эмбеддинга (с кэшем). Результат смешивается с
обычным поиском, а не заменяет его: точный артикул всегда важнее похожести.
Без ключа или при сбое NVIDIA поиск работает как раньше.
"""

import array
import base64
import json
import logging
import math
from collections import OrderedDict
from pathlib import Path

from app.modules.catalog.models import Product

log = logging.getLogger(__name__)

DESCRIPTION_CHARS = 300


def product_passage(p: Product) -> str:
    """Текст карточки для эмбеддинга: название, бренд, раздел и ключевые характеристики."""
    attrs = "; ".join(f"{a.label}: {a.value}" for a in p.attributes if a.value)
    parts = [p.name, p.brand or "", " / ".join(p.category_path).replace("_", " "), attrs,
             p.description[:DESCRIPTION_CHARS]]
    return "\n".join(x for x in parts if x)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def _pack(vector: list[float]) -> str:
    return base64.b64encode(array.array("f", vector).tobytes()).decode("ascii")


def _unpack(value: str) -> list[float]:
    return array.array("f", base64.b64decode(value)).tolist()


class SemanticIndex:
    def __init__(self, model: str, vectors: dict[int, list[float]], client=None, min_score: float = 0.2,
                 cache_size: int = 512):
        self.model = model
        self.vectors = {pid: _normalize(v) for pid, v in vectors.items()}
        self.client = client
        self.min_score = min_score
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_size = cache_size

    # --- файл индекса ---

    @classmethod
    def load(cls, path: Path, client=None, min_score: float = 0.2) -> "SemanticIndex | None":
        path = Path(path)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        vectors = {int(pid): _unpack(v) for pid, v in data["vectors"].items()}
        return cls(data["model"], vectors, client, min_score)

    def save(self, path: Path) -> None:
        payload = {"model": self.model, "count": len(self.vectors),
                   "vectors": {str(pid): _pack(v) for pid, v in sorted(self.vectors.items())}}
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def build(cls, products: list[Product], client, model: str, batch: int = 32) -> "SemanticIndex":
        vectors: dict[int, list[float]] = {}
        for start in range(0, len(products), batch):
            chunk = products[start:start + batch]
            for product, vector in zip(chunk, client.embed([product_passage(p) for p in chunk], model, "passage")):
                vectors[product.id] = vector
        return cls(model, vectors, client)

    # --- поиск ---

    def _query_vector(self, query: str, session_id: str | None) -> list[float] | None:
        key = query.strip().lower()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if self.client is None:
            return None
        from app.adapters.nvidia.client import NvidiaError
        try:
            vector = _normalize(self.client.embed([query], self.model, "query", session_id=session_id)[0])
        except NvidiaError as e:
            log.warning("semantic query skipped: %s", e)
            return None
        self._cache[key] = vector
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return vector

    def search(self, query: str, limit: int = 8, session_id: str | None = None) -> list[tuple[int, float]]:
        if not self.vectors or not query.strip():
            return []
        q = self._query_vector(query, session_id)
        if q is None:
            return []
        scored = [(pid, sum(a * b for a, b in zip(q, v))) for pid, v in self.vectors.items()]
        scored = [s for s in scored if s[1] >= self.min_score]
        scored.sort(key=lambda s: (-s[1], s[0]))
        return scored[:limit]
