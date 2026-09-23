"""Тесты на настоящем PostgreSQL. Нужен TEST_DATABASE_URL, иначе пропускаются."""

import os
import psycopg
import pytest

from app.adapters.ekt.client import EktError, EktUnavailable
from app.adapters.memory import SYNTHETIC_CATALOG, InMemoryCartStore, InMemoryProposalStore
from app.modules.actions.service import ActionService
from app.modules.catalog.models import StockStatus
from app.modules.search.alternatives import AlternativeService
from app.modules.search.service import MatchKind, SearchService

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL не задан")


@pytest.fixture
def pool():
    from app.infrastructure.db import create_pool, migrate
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrate(URL)
    p = create_pool(URL)
    yield p
    p.close()


@pytest.fixture
def catalog(pool):
    from app.adapters.postgres.catalog import PostgresCatalog
    from app.modules.catalog.sync import import_file
    assert import_file(pool, SYNTHETIC_CATALOG).status == "complete"
    return PostgresCatalog(pool)


class FakeEkt:
    def __init__(self, pages=None, details=None, error=None):
        self.pages = pages or {}
        self.details = details or {}
        self.error = error

    def list_page(self, page):
        if self.error:
            raise self.error
        return {"items": [{"id": i} for i in self.pages.get(page, [])]}

    def get_detail(self, pid):
        if self.error:
            raise self.error
        return self.details[pid]


def raw(pid, price=1000, qty=5, name="АВ ВА47-29 1P 16А C IEK"):
    return {"id": pid, "name": name, "article": f"{pid}_", "price": price, "quantity": qty,
            "url": "https://ekt.kz/catalog/a/b/x/", "description": "", "image": None, "offers": [],
            "stores": [{"id": 13, "name": "Алматы", "quantity": qty}], "properties": {"NOMINALNYY_TOK": "16А"}}


def test_migrations_are_idempotent(pool):
    from app.infrastructure.db import migrate
    assert migrate(URL) == []


def test_lookup_and_search(catalog):
    search = SearchService(catalog)
    target = next(p for p in catalog.search_text("RX3 1P 16А Legrand", 5) if "RX3 1P 16А" in p.name)
    for identifier in (target.article, target.supplier_article, target.barcode, target.article.upper()):
        match = search.search(identifier)[0]
        assert match.product.id == target.id and match.kind is MatchKind.EXACT
    assert "Розетка" in catalog.search_text("розетки белые", 3)[0].name
    assert catalog.search_text("кабель 3х2,5", 1)[0].attribute("cross_section").value == "3х2,5"


def test_stored_product_keeps_quality_rules(catalog):
    conflict = catalog.search_text("DRX250", 1)[0]
    assert "conflict:rated_current" in conflict.warnings
    defect = next(p for p in catalog.search_text("светильник 18W", 5) if "18W" in p.name and "MEGALIGHT" in p.name)
    assert defect.stock.status is StockStatus.NOT_SELLABLE


def test_alternatives_from_db(catalog):
    source = next(p for p in catalog.search_text("RX3 1P 16А", 5) if "RX3 1P 16А" in p.name)
    alts = AlternativeService(catalog).find(source).alternatives
    assert alts and all(a.product.attribute("rated_current").value == "16А" for a in alts)


def test_fresh_reads_live_and_updates(pool):
    from app.adapters.postgres.catalog import PostgresCatalog, upsert_product
    with pool.connection() as conn:
        upsert_product(conn, raw(7, price=1000), "ekt")
    live = FakeEkt(details={7: raw(7, price=1200)})
    catalog = PostgresCatalog(pool, live)
    assert catalog.get_product(7).price == 1000
    assert catalog.get_product(7, fresh=True).price == 1200
    assert catalog.get_product(7).price == 1200


def test_fresh_failure_is_unknown_not_zero(pool):
    from app.adapters.postgres.catalog import PostgresCatalog, upsert_product
    with pool.connection() as conn:
        upsert_product(conn, raw(7), "ekt")
    catalog = PostgresCatalog(pool, FakeEkt(error=EktUnavailable("timeout")))
    product = catalog.get_product(7, fresh=True)
    assert product.stock.status is StockStatus.UNKNOWN
    assert product.stock.sellable_quantity is None
    actions = ActionService(catalog, InMemoryProposalStore(), InMemoryCartStore(), "/cart")
    assert actions.prepare_proposal("s1", [(7, 1)]).problems[0].code == "stock_unknown"


def test_older_snapshot_does_not_overwrite_newer(pool):
    from datetime import datetime, timedelta, timezone
    from app.adapters.postgres.catalog import PostgresCatalog, upsert_product
    now = datetime.now(timezone.utc)
    with pool.connection() as conn:
        upsert_product(conn, raw(7, price=1500), "ekt", now)
        upsert_product(conn, raw(7, price=900), "ekt", now - timedelta(minutes=5))
    assert PostgresCatalog(pool).get_product(7).price == 1500


def test_import_ekt_statuses(pool):
    from app.modules.catalog.sync import import_ekt
    details = {i: raw(i) for i in range(1, 5)}
    complete = import_ekt(pool, FakeEkt(pages={1: [1, 2], 2: [3, 4], 3: []}, details=details), max_pages=10)
    assert (complete.status, complete.upserted) == ("complete", 4)
    limited = import_ekt(pool, FakeEkt(pages={1: [1, 2], 2: [3, 4]}, details=details), max_pages=1)
    assert limited.status == "partial"
    repeated = import_ekt(pool, FakeEkt(pages={1: [1, 2], 2: [1, 2]}, details=details), max_pages=5)
    assert repeated.status == "partial" and "repeats" in repeated.error
    failed = import_ekt(pool, FakeEkt(error=EktError("down")), max_pages=5)
    assert failed.status == "failed"


def test_import_ekt_skips_broken_card(pool):
    from app.modules.catalog.sync import import_ekt

    class Flaky(FakeEkt):
        def get_detail(self, pid):
            if pid == 2:
                raise EktUnavailable("timeout")
            return raw(pid)

    report = import_ekt(pool, Flaky(pages={1: [1, 2, 3], 2: []}), max_pages=5)
    assert (report.status, report.upserted, report.failed) == ("partial", 2, 1)


def test_http_api_reads_catalog_from_postgres(catalog, tmp_path):
    from fastapi.testclient import TestClient

    from app.bootstrap import build_container
    from app.core.config import Settings
    from app.main import create_app
    from conftest import authenticate

    services = build_container(Settings(_env_file=None, app_env="test", integration_mode="catalog_db",
                                        database_url=URL, local_db_path=tmp_path / "state.sqlite3"))
    services.store.initialize()
    assert services.capabilities["catalog"] == "synthetic_demo"
    with TestClient(create_app(container=services)) as client:
        headers = authenticate(client)
        found = client.get("/api/v1/products", params={"query": "990100001_"}).json()["data"]
        assert found["coverage"] == "complete"
        item = found["items"][0]
        assert item["article_original"] == "990100001_"
        prepared = client.post("/api/v1/cart/proposals", headers={**headers, "Idempotency-Key": "proposal-0001"},
                               json={"items": [{"product_id": item["id"], "quantity": 2}]})
        assert prepared.status_code == 202, prepared.text
        draft = prepared.json()["data"]
        applied = client.post(f"/api/v1/proposals/{draft['id']}/confirm", json={"version": draft["version"]},
                              headers={**headers, "Idempotency-Key": "confirm-0001"})
        assert applied.json()["data"]["status"] == "applied"
        assert client.get("/api/v1/cart").json()["data"]["items"][0]["quantity"] == 2


@pytest.fixture
def empty_db():
    """Совсем пустая база: без таблиц и без миграций, как у нового участника или проверяющего."""
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return URL


def _container(tmp_path, **extra):
    from app.bootstrap import build_container
    from app.core.config import Settings
    return build_container(Settings(_env_file=None, app_env="test", integration_mode="catalog_db", database_url=URL,
                                    local_db_path=tmp_path / "state.sqlite3", **extra))


def _count(sql):
    with psycopg.connect(URL) as conn:
        return conn.execute(sql).fetchone()[0]


def test_catalog_db_starts_on_empty_database(empty_db, tmp_path):
    import asyncio
    services = _container(tmp_path)
    assert _count("SELECT count(*) FROM products") == 281
    assert asyncio.run(services.catalog.search("990100015_", 3)).items[0].article_original == "990100015_"
    _container(tmp_path)  # повторный старт не грузит ещё раз
    assert _count("SELECT count(*) FROM catalog_sync_runs") == 1


def test_seed_does_not_touch_imported_catalog(empty_db, tmp_path):
    from app.adapters.postgres.catalog import upsert_product
    from app.infrastructure.db import migrate
    migrate(URL)
    with psycopg.connect(URL) as conn:
        upsert_product(conn, raw(7), "ekt")
    services = _container(tmp_path)
    assert _count("SELECT count(*) FROM products") == 1
    assert services.capabilities["catalog"] == "ready"


def test_seed_can_be_disabled(empty_db, tmp_path):
    _container(tmp_path, catalog_db_seed_demo=False)
    assert _count("SELECT count(*) FROM products") == 0


def test_parallel_seed_loads_once(empty_db):
    from concurrent.futures import ThreadPoolExecutor
    from app.infrastructure.db import create_pool, migrate
    from app.modules.catalog.sync import seed_if_empty
    migrate(URL)
    pools = [create_pool(URL) for _ in range(3)]
    try:
        with ThreadPoolExecutor(3) as ex:
            reports = list(ex.map(lambda p: seed_if_empty(p, SYNTHETIC_CATALOG), pools))
    finally:
        for p in pools:
            p.close()
    assert sum(r is not None for r in reports) == 1
    assert _count("SELECT count(*) FROM catalog_sync_runs") == 1
