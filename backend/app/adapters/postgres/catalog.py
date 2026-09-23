"""Каталог в PostgreSQL. Реализует CatalogReader.

В таблице лежит сырая карточка источника, Product собирается из неё через
product_from_source(). fresh=True перечитывает карточку из EKT; если EKT не
ответил, возвращаем сохранённую карточку с остатком UNKNOWN, а не нулём.
"""

import logging
import re
from dataclasses import replace
from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.adapters.ekt.client import EktClient, EktError, EktNotFound
from app.modules.catalog.models import Product, Stock, StockStatus
from app.modules.catalog.quality import product_from_source

log = logging.getLogger(__name__)

UPSERT = """
INSERT INTO products (id, source, article, supplier_article, barcode, name, brand, category_path, price, unit,
                      stock_status, sellable_quantity, warnings, raw, fetched_at, updated_at)
VALUES (%(id)s, %(source)s, %(article)s, %(supplier_article)s, %(barcode)s, %(name)s, %(brand)s,
        %(category_path)s, %(price)s, %(unit)s, %(stock_status)s, %(sellable_quantity)s, %(warnings)s,
        %(raw)s, %(fetched_at)s, now())
ON CONFLICT (id) DO UPDATE SET
    source = excluded.source, article = excluded.article, supplier_article = excluded.supplier_article,
    barcode = excluded.barcode, name = excluded.name, brand = excluded.brand,
    category_path = excluded.category_path, price = excluded.price, unit = excluded.unit,
    stock_status = excluded.stock_status, sellable_quantity = excluded.sellable_quantity,
    warnings = excluded.warnings, raw = excluded.raw, fetched_at = excluded.fetched_at, updated_at = now()
WHERE products.fetched_at <= excluded.fetched_at
"""

COLUMNS = "raw, fetched_at"


def upsert_product(conn, raw: dict, source: str, fetched_at: datetime | None = None) -> Product:
    """Проверяет карточку правилами качества и сохраняет. Старый снимок не затирает новый."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    product = product_from_source(raw, fetched_at)
    conn.execute(UPSERT, {
        "id": product.id, "source": source, "article": product.article,
        "supplier_article": product.supplier_article, "barcode": product.barcode, "name": product.name,
        "brand": product.brand, "category_path": list(product.category_path), "price": product.price,
        "unit": product.unit, "stock_status": product.stock.status.value,
        "sellable_quantity": product.stock.sellable_quantity, "warnings": list(product.warnings),
        "raw": Jsonb(raw), "fetched_at": fetched_at,
    })
    return product


def _unknown_stock(product: Product) -> Product:
    return replace(product, stock=Stock(StockStatus.UNKNOWN, None, None, product.stock.stores),
                   warnings=(*product.warnings, "stock_unverified"))


class PostgresCatalog:
    def __init__(self, pool: ConnectionPool, live: EktClient | None = None):
        self.pool = pool
        self.live = live

    def _rows(self, sql: str, params) -> list[Product]:
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [product_from_source(raw, fetched_at) for raw, fetched_at in rows]

    def get_product(self, product_id: int, fresh: bool = False) -> Product | None:
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {COLUMNS}, source FROM products WHERE id = %s", (product_id,)).fetchone()
        stored = product_from_source(row[0], row[1]) if row else None
        source = row[2] if row else None
        if not fresh or self.live is None or source == "synthetic":
            return stored
        try:
            raw = self.live.get_detail(product_id)
        except EktNotFound:
            return None if stored is None else _unknown_stock(stored)
        except EktError as e:
            log.warning("ekt detail %s failed: %s", product_id, e)
            return None if stored is None else _unknown_stock(stored)
        with self.pool.connection() as conn:
            return upsert_product(conn, raw, "ekt")

    def find_by_identifier(self, value: str) -> list[Product]:
        v = value.strip()
        return self._rows(
            f"SELECT {COLUMNS} FROM products WHERE lower(article) = lower(%s) "
            f"OR lower(supplier_article) = lower(%s) OR barcode = %s ORDER BY id LIMIT 20",
            (v, v, v),
        )

    def search_text(self, query: str, limit: int = 20) -> list[Product]:
        words = re.findall(r"\w+", query.lower())[:12]
        if not words:
            return []
        # OR по словам: клиент редко пишет название так же, как в каталоге.
        ts_query = " or ".join(words)
        return self._rows(
            f"""SELECT {COLUMNS} FROM products,
                       websearch_to_tsquery('russian', %(ts)s) AS q
                WHERE search_vector @@ q OR lower(name) %% %(text)s
                ORDER BY ts_rank_cd(search_vector, q) + similarity(lower(name), %(text)s) DESC, id
                LIMIT %(limit)s""",
            {"ts": ts_query, "text": " ".join(words), "limit": min(limit, 50)},
        )

    def list_category(self, category_path: tuple[str, ...], limit: int = 200) -> list[Product]:
        return self._rows(f"SELECT {COLUMNS} FROM products WHERE category_path = %s ORDER BY id LIMIT %s",
                          (list(category_path), limit))
