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

    def ping(self) -> None:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1").fetchone()

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

    def category_counts(self) -> list[tuple[tuple[str, ...], int]]:
        with self.pool.connection() as conn:
            rows = conn.execute("""SELECT category_path, count(*) FROM products
                WHERE cardinality(category_path)>0 GROUP BY category_path ORDER BY category_path""").fetchall()
        return [(tuple(path), count) for path, count in rows]

    def browse(self, *, query="", category="", brand="", stock_only=False, min_price=None, max_price=None,
               sort="relevance", page=1, page_size=12):
        """Parameterized storefront filters and page from one consistent PG snapshot.

        Only imported rows are queried. Browsing never triggers live EKT requests.
        Brand facets use all filters except the selected brand itself.
        """
        query = query.strip()[:256]
        words = re.findall(r"\w+", query.lower())[:12]
        params = {"query": query, "ts": " or ".join(words), "text": " ".join(words),
                  "brand": brand, "category": category.split("/") if category else [],
                  "min_price": min_price, "max_price": max_price,
                  "limit": page_size, "offset": (page - 1) * page_size}
        exact = "(id::text=%(query)s OR lower(article)=lower(%(query)s) OR lower(supplier_article)=lower(%(query)s) OR lower(barcode)=lower(%(query)s))"
        lexical = "websearch_to_tsquery('russian', %(ts)s)"
        score = f"CASE WHEN {exact} THEN 10000 ELSE 0 END + ts_rank_cd(search_vector,{lexical}) + similarity(lower(name),%(text)s)" if query else "0"
        filters = []
        if query:
            filters.append(f"({exact} OR search_vector @@ {lexical} OR lower(name) %% %(text)s)")
        if category:
            filters.append("category_path[1:cardinality(%(category)s::text[])]=%(category)s::text[]")
        if stock_only:
            filters.append("stock_status='in_stock' AND sellable_quantity>0")
        if min_price is not None:
            filters.append("price >= %(min_price)s")
        if max_price is not None:
            filters.append("price <= %(max_price)s")
        base_where = " AND ".join(filters) if filters else "TRUE"
        where = base_where + (" AND lower(brand)=lower(%(brand)s)" if brand else "")
        order = {"relevance": "score DESC,id", "price_asc": "price ASC NULLS LAST,id",
                 "price_desc": "price DESC NULLS LAST,id", "name": "lower(name),id"}[sort]
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                total = conn.execute(f"SELECT count(*) FROM products WHERE {where}", params).fetchone()[0]
                brand_rows = conn.execute(f"""SELECT DISTINCT brand FROM products
                    WHERE {base_where} AND brand IS NOT NULL AND brand<>'' ORDER BY brand""", params).fetchall()
                rows = conn.execute(f"""SELECT {COLUMNS}, {score} AS score FROM products WHERE {where}
                    ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s""", params).fetchall()
        return ([product_from_source(raw, fetched_at) for raw, fetched_at, _ in rows],
                total, [row[0] for row in brand_rows])
