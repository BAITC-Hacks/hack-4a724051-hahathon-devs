"""Durable local catalog shared by storefront, assistant and demo cart validation.

Participant 1 owns catalog ingestion/data persistence. Import always runs the
shared quality mapper. Synthetic fixture seeding never overwrites existing rows.
This adapter reads current SQLite facts on each call; it does not refresh EKT.
"""
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.modules.catalog.models import (
    Attribute, AttributeStatus, Certificate, Observation, Product, Stock, StockStatus, StoreStock,
)
from app.modules.catalog.quality import product_from_source


def _serialize(product: Product) -> str:
    return json.dumps(asdict(product), ensure_ascii=False, default=str)


def _deserialize(value: str) -> Product:
    data = json.loads(value)
    data["price"] = Decimal(data["price"]) if data["price"] is not None else None
    data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
    data["category_path"] = tuple(data["category_path"])
    data["warnings"] = tuple(data["warnings"])
    data["attributes"] = tuple(Attribute(**{
        **a, "status": AttributeStatus(a["status"]),
        "observations": tuple(Observation(**o) for o in a["observations"]),
    }) for a in data["attributes"])
    data["certificates"] = tuple(Certificate(**c) for c in data["certificates"])
    data["stock"] = Stock(**{**data["stock"], "status": StockStatus(data["stock"]["status"]),
                             "stores": tuple(StoreStock(**s) for s in data["stock"]["stores"])})
    return Product(**data)


def _fold(value):
    return str(value or "").casefold().replace("ё", "е")


def _decimal_compare(left, right):
    if left is None or right is None:
        return None
    a, b = Decimal(left), Decimal(right)
    return (a > b) - (a < b)


class SQLiteCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connection()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS catalog_products (
                    id INTEGER PRIMARY KEY, raw_json TEXT NOT NULL, product_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, article_key TEXT NOT NULL, supplier_key TEXT NOT NULL,
                    name_key TEXT NOT NULL, description_key TEXT NOT NULL, category_path TEXT NOT NULL,
                    brand TEXT, brand_key TEXT NOT NULL, price_amount TEXT, stock_status TEXT NOT NULL,
                    sellable_quantity INTEGER);
                CREATE INDEX IF NOT EXISTS catalog_category ON catalog_products(category_path);
                CREATE INDEX IF NOT EXISTS catalog_brand ON catalog_products(brand_key);
                CREATE TABLE IF NOT EXISTS catalog_identifiers (
                    product_id INTEGER NOT NULL REFERENCES catalog_products(id) ON DELETE CASCADE,
                    identifier TEXT NOT NULL, PRIMARY KEY(product_id,identifier));
                CREATE INDEX IF NOT EXISTS catalog_identifier_lookup ON catalog_identifiers(identifier);
            """)

    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.create_function("decimal_compare", 2, _decimal_compare, deterministic=True)
        db.create_collation("DECIMAL", _decimal_compare)
        return db

    @staticmethod
    def _write(db, raw, product):
        db.execute("""INSERT INTO catalog_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET raw_json=excluded.raw_json, product_json=excluded.product_json,
            fetched_at=excluded.fetched_at, article_key=excluded.article_key, supplier_key=excluded.supplier_key,
            name_key=excluded.name_key, description_key=excluded.description_key,
            category_path=excluded.category_path, brand=excluded.brand, brand_key=excluded.brand_key,
            price_amount=excluded.price_amount, stock_status=excluded.stock_status,
            sellable_quantity=excluded.sellable_quantity""",
                   (product.id, json.dumps(raw, ensure_ascii=False, default=str), _serialize(product),
                    product.fetched_at.isoformat(), _fold(product.article), _fold(product.supplier_article),
                    _fold(product.name), _fold(product.description), "/".join(product.category_path),
                    product.brand, _fold(product.brand), str(product.price) if product.price is not None else None,
                    product.stock.status.value, product.stock.sellable_quantity))
        identifiers = {_fold(product.article), _fold(product.supplier_article), _fold(product.barcode)}
        props = raw.get("properties") or {}
        for value in (raw.get("barcode"), raw.get("barcodes"), props.get("SHTRIKH_KOD"), props.get("SHTRIKHKOD")):
            identifiers.update(_fold(item) for item in value) if isinstance(value, list) else identifiers.add(_fold(value))
        db.execute("DELETE FROM catalog_identifiers WHERE product_id=?", (product.id,))
        db.executemany("INSERT INTO catalog_identifiers VALUES (?, ?)",
                       [(product.id, value) for value in identifiers if value])

    def seed_if_empty(self, fixture: Path) -> bool:
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM catalog_products LIMIT 1").fetchone():
                return False
            items = json.loads(Path(fixture).read_text(encoding="utf-8"))["items"]
            fetched_at = datetime.now(timezone.utc)
            for raw in items:
                self._write(db, raw, product_from_source(raw, fetched_at=fetched_at))
            return True

    def import_product(self, raw: dict, fetched_at: datetime | None = None) -> Product:
        product = product_from_source(raw, fetched_at=fetched_at)
        with closing(self._connection()) as db, db:
            self._write(db, raw, product)
        return product

    def replace_product(self, product: Product) -> None:
        """Persist an explicit domain correction; retained for existing adapter tests.

        Original import JSON remains as provenance; the normalized record contains
        the correction and indexes are updated in the same transaction.
        """
        with closing(self._connection()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT raw_json FROM catalog_products WHERE id=?", (product.id,)).fetchone()
            self._write(db, json.loads(row[0]) if row else {"id": product.id}, product)

    def get_product(self, product_id: int, fresh: bool = False) -> Product | None:
        # Both normal/fresh reads fetch the current DB record; no process-local cache.
        with closing(self._connection()) as db:
            row = db.execute("SELECT product_json FROM catalog_products WHERE id=?", (product_id,)).fetchone()
        return _deserialize(row[0]) if row else None

    def find_by_identifier(self, value: str) -> list[Product]:
        with closing(self._connection()) as db:
            rows = db.execute("""SELECT p.product_json FROM catalog_products p JOIN catalog_identifiers i
                ON i.product_id=p.id WHERE i.identifier=? ORDER BY p.id""", (_fold(value.strip()),)).fetchall()
        return [_deserialize(row[0]) for row in rows]

    @staticmethod
    def _score(query: str):
        query = _fold(query.strip())[:256]
        tokens = list(dict.fromkeys(re.findall(r"[\w,]+", query)))[:20]
        if not query:
            return "0", []
        expression = ["CASE WHEN CAST(id AS TEXT)=? OR id IN (SELECT product_id FROM catalog_identifiers WHERE identifier=?) THEN 10000 ELSE 0 END"]
        args = [query, query]
        for token in tokens:
            expression.append("CASE WHEN instr(name_key,?)>0 THEN 3 ELSE 0 END + CASE WHEN instr(description_key,?)>0 THEN 1 ELSE 0 END")
            args.extend([token, token])
        return "(" + " + ".join(expression) + ")", args

    def search_text(self, query: str, limit: int = 20) -> list[Product]:
        if not query.strip():
            return []
        score, args = self._score(query)
        with closing(self._connection()) as db:
            rows = db.execute(f"SELECT product_json, {score} AS score FROM catalog_products WHERE {score}>0 ORDER BY score DESC, id LIMIT ?",
                              [*args, *args, max(0, min(limit, 200))]).fetchall()
        return [_deserialize(row[0]) for row in rows]

    def list_category(self, category_path: tuple[str, ...], limit: int = 200) -> list[Product]:
        with closing(self._connection()) as db:
            rows = db.execute("SELECT product_json FROM catalog_products WHERE category_path=? ORDER BY id LIMIT ?",
                              ("/".join(category_path), max(0, min(limit, 200)))).fetchall()
        return [_deserialize(row[0]) for row in rows]

    def category_counts(self) -> list[tuple[tuple[str, ...], int]]:
        with closing(self._connection()) as db:
            rows = db.execute("SELECT category_path,count(*) FROM catalog_products WHERE category_path<>'' GROUP BY category_path ORDER BY category_path").fetchall()
        return [(tuple(row[0].split("/")), row[1]) for row in rows]

    def browse(self, *, query="", category="", brand="", stock_only=False, min_price=None, max_price=None,
               sort="relevance", page=1, page_size=12):
        score, score_args = self._score(query)
        filters, args = [], []
        if query.strip():
            filters.append(f"{score}>0")
            args.extend(score_args)
        if category:
            filters.append("(category_path=? OR substr(category_path,1,length(?)+1)=? || '/')")
            args.extend([category, category, category])
        if stock_only:
            filters.append("stock_status='in_stock' AND sellable_quantity>0")
        for price, op in ((min_price, ">="), (max_price, "<=")):
            if price is not None:
                filters.append(f"decimal_compare(price_amount,?) {op} 0")
                args.append(str(price))
        base_where = " AND ".join(filters) if filters else "1"
        where = base_where
        final_args = list(args)
        if brand:
            where += " AND brand_key=?"
            final_args.append(_fold(brand))
        order = {"relevance": "score DESC,id", "price_asc": "price_amount IS NULL,price_amount COLLATE DECIMAL ASC,id",
                 "price_desc": "price_amount IS NULL,price_amount COLLATE DECIMAL DESC,id", "name": "name_key,id"}[sort]
        with closing(self._connection()) as db, db:
            # One read snapshot keeps count, facets and page mutually consistent.
            db.execute("BEGIN")
            total = db.execute(f"SELECT count(*) FROM catalog_products WHERE {where}", final_args).fetchone()[0]
            brands = [row[0] for row in db.execute(f"SELECT DISTINCT brand FROM catalog_products WHERE {base_where} AND brand IS NOT NULL AND brand<>'' ORDER BY brand_key,brand", args)]
            rows = db.execute(f"SELECT product_json,{score} AS score FROM catalog_products WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                              [*score_args, *final_args, page_size, (page - 1) * page_size]).fetchall()
        return [_deserialize(row[0]) for row in rows], total, brands
