"""PostgreSQL browse wiring without a server, credentials, or network requests."""
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.adapters.postgres.catalog import PostgresCatalog
from app.bootstrap import build_container
from app.core.config import Settings
from app.integrations.domain import DomainCatalogAdapter
from app.main import create_app
from app.modules.catalog.browse import CatalogBrowseReader, CatalogBrowseService
from conftest import authenticate


RAW = {"id": 42, "article": "PG-42", "name": "Stored PostgreSQL product", "price": 1234,
       "url": "/catalog/kabel_provod/kabel_silovoy/pg-42/", "quantity": 2,
       "stores": [{"id": 1, "name": "Алматы", "quantity": 2}],
       "properties": {"TORGOVAYA_MARKA": "PG Brand"}}


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class RecordingConnection:
    def __init__(self):
        self.statements = []
        self.in_transaction = False

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params, self.in_transaction))
        if sql.startswith("SELECT category_path"):
            return Rows([(["kabel_provod", "kabel_silovoy"], 1)])
        if sql.startswith("SELECT count(*)"):
            return Rows([(1,)])
        if sql.startswith("SELECT DISTINCT brand"):
            return Rows([("PG Brand",)])
        if sql.startswith("SELECT raw, fetched_at,"):
            return Rows([(RAW, datetime(2026, 9, 23, tzinfo=timezone.utc), 10000)])
        return Rows([])


class RecordingPool:
    def __init__(self):
        self.conn = RecordingConnection()

    @contextmanager
    def connection(self):
        yield self.conn


def test_postgres_browse_uses_bound_filters_and_one_read_snapshot():
    pool = RecordingPool()
    reader = PostgresCatalog(pool)
    assert isinstance(reader, CatalogBrowseReader)
    injection = "'; DROP TABLE products;--"
    items, total, brands = reader.browse(query=injection, category="kabel_provod", brand=injection,
                                       min_price=1000, max_price=2000, stock_only=True, page=3, page_size=5)
    assert total == 1 and brands == ["PG Brand"] and items[0].id == 42
    statements = pool.conn.statements
    assert "REPEATABLE READ READ ONLY" in statements[0][0]
    assert all(active for _, _, active in statements)
    assert all(injection not in sql for sql, _, _ in statements)
    assert statements[-1][1]["query"] == injection
    assert statements[-1][1]["offset"] == 10
    assert statements[-1][1]["category"] == ["kabel_provod"]
    assert "lower(brand)=lower(%(brand)s)" in statements[1][0]
    assert "lower(brand)=lower(%(brand)s)" not in statements[2][0]


def test_postgres_catalog_supports_category_tree_and_correct_source_labels():
    service = CatalogBrowseService(PostgresCatalog(RecordingPool()), source_ref="ekt_catalog", demo=False)
    categories = service.categories()
    assert len(categories.items) == 12
    assert categories.items[0].count == 1
    page = service.products()
    assert page.items[0].sources[0].ref == "ekt_catalog"
    assert "synthetic_demo_data" not in page.warnings
    assert "synthetic_demo_data" not in page.items[0].warnings


def test_http_browse_accepts_postgres_reader_without_sqlite_type_guard(tmp_path):
    catalog = DomainCatalogAdapter(PostgresCatalog(RecordingPool()), "ekt_catalog", False)
    services = build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "state.sqlite3"),
                               catalog=catalog)
    with TestClient(create_app(container=services)) as client:
        authenticate(client)
        response = client.get("/api/v1/catalog/products")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"][0]["article_original"] == "PG-42"
        assert "synthetic_demo_data" not in response.json()["data"]["warnings"]
        assert client.get("/api/v1/catalog/categories").status_code == 200
