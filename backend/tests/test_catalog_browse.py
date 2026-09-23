import asyncio
import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.storage.catalog import SQLiteCatalog
from app.bootstrap import build_container
from app.core.config import Settings
from app.main import create_app
from app.modules.catalog.browse import CATEGORY_LABELS
from app.modules.catalog.models import StockStatus
from conftest import authenticate, conversation, submit

FIXTURE = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ekt_products.json"


@pytest.fixture
def browse(tmp_path):
    services = build_container(Settings(_env_file=None, app_env="test", integration_mode="synthetic",
                                        local_db_path=tmp_path / "browse.sqlite3"))
    with TestClient(create_app(container=services)) as client:
        headers = authenticate(client)
        yield services, client, headers


def page(browse, **params):
    response = browse[1].get("/api/v1/catalog/products", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_import_is_quality_checked_durable_and_seed_does_not_reset_corrections(browse):
    services, _, _ = browse
    catalog = services.catalog.catalog
    assert isinstance(catalog, SQLiteCatalog)
    assert catalog.get_product(900044).attribute("rated_current").status.value == "conflict"
    original = catalog.get_product(900003)
    corrected = replace(original, price=Decimal("1234.5678"), name="Corrected durable catalog name")
    catalog.replace_product(corrected)
    restarted = build_container(services.settings)
    assert restarted.catalog.catalog.get_product(original.id, fresh=True) == corrected
    assert not restarted.catalog.catalog.seed_if_empty(FIXTURE)
    with sqlite3.connect(services.settings.local_db_path) as db:
        raw, fetched = db.execute("SELECT raw_json,fetched_at FROM catalog_products WHERE id=?", (original.id,)).fetchone()
        assert json.loads(raw)["id"] == original.id
        assert fetched == original.fetched_at.isoformat()


def test_categories_have_twelve_real_top_levels_and_aggregate_counts(browse):
    response = browse[1].get("/api/v1/catalog/categories")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["coverage"] == "partial"
    assert len(data["items"]) == 12
    assert {node["path"][0]: node["name"] for node in data["items"]} == CATEGORY_LABELS
    assert sum(node["count"] for node in data["items"]) == page(browse)["total"]
    assert any(node["count"] == 0 for node in data["items"])
    for node in data["items"]:
        assert sum(child["count"] for child in node["children"]) == node["count"]
        assert all(child["path"][0] == node["path"][0] for child in node["children"])


def test_server_pagination_returns_all_rows_once_in_stable_order(browse):
    first = page(browse, page_size=7)
    assert len(first["items"]) == 7
    ids = []
    for number in range(1, first["pages"] + 1):
        current = page(browse, page_size=7, page=number)
        assert current["total"] == first["total"]
        ids.extend(item["id"] for item in current["items"])
    assert len(ids) == len(set(ids)) == first["total"]
    assert ids == sorted(ids)
    assert page(browse, page_size=7, page=first["pages"] + 1)["items"] == []


def test_category_prefix_brand_stock_and_price_filters_use_database(browse):
    data = page(browse, category="nizkovoltnaya_apparatura", brand="iek", stock_only=True,
                min_price="1000", max_price="5000", page_size=24, sort="price_asc")
    assert data["items"]
    assert all(item["category_path"][0] == "nizkovoltnaya_apparatura" for item in data["items"])
    assert all(item["brand"] == "IEK" and item["stock_status"] == "available" for item in data["items"])
    prices = [Decimal(item["price_amount"]) for item in data["items"]]
    assert prices == sorted(prices)
    assert all(1000 <= price <= 5000 for price in prices)
    assert "IEK" in data["brands"]
    # Brand facets apply other filters and retain brands when one is selected.
    assert data["brands"] == page(browse, category="nizkovoltnaya_apparatura", stock_only=True,
                                    min_price="1000", max_price="5000")["brands"]
    category = page(browse, category="kabel_provod/kabel_silovoy")
    assert category["total"] == 3
    assert page(browse, category="kabel_provod/kabel")["total"] == 0


def test_numeric_prices_are_sorted_exactly_and_missing_prices_last(browse):
    catalog = browse[0].catalog.catalog
    base = catalog.get_product(900003)
    for pid, amount in [(800001, "900000000000.0002"), (800002, "900000000000.0001"), (800003, None)]:
        catalog.replace_product(replace(base, id=pid, brand="Precision test", price=Decimal(amount) if amount else None))
    ascending = page(browse, brand="Precision test", sort="price_asc")
    descending = page(browse, brand="Precision test", sort="price_desc")
    assert [item["id"] for item in ascending["items"]] == [800002, 800001, 800003]
    assert [item["id"] for item in descending["items"]] == [800001, 800002, 800003]
    filtered = page(browse, brand="Precision test", min_price="900000000000.0002")
    assert [item["id"] for item in filtered["items"]] == [800001]


def test_exact_identifiers_barcodes_and_fresh_reads_cross_instances(browse):
    services = browse[0]
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"][2]
    raw["barcode"] = "TEST-BARCODE-900003"
    first = services.catalog.catalog
    second = SQLiteCatalog(services.settings.local_db_path)
    imported = second.import_product(raw)
    assert first.find_by_identifier(raw["barcode"].lower())[0].id == imported.id
    assert page(browse, query=raw["barcode"].lower())["items"][0]["id"] == imported.id
    assert first.find_by_identifier(imported.supplier_article.lower())[0].id == imported.id
    assert page(browse, query=imported.article)["items"][0]["id"] == imported.id
    changed = replace(imported, stock=replace(imported.stock, status=StockStatus.UNKNOWN, sellable_quantity=None))
    second.replace_product(changed)
    assert first.get_product(imported.id, fresh=True).stock.status is StockStatus.UNKNOWN
    assert not page(browse, query=imported.article, stock_only=True)["items"]


def test_storefront_product_api_and_assistant_share_persisted_facts(browse):
    services, client, headers = browse
    source = services.catalog.catalog.get_product(900003)
    changed = replace(source, price=Decimal("2222.50"), brand="Persisted brand", image="javascript:alert(1)")
    another_process = SQLiteCatalog(services.settings.local_db_path)
    another_process.replace_product(changed)
    storefront = page(browse, query=source.article)["items"][0]
    direct = client.get(f"/api/v1/products/{source.id}").json()["data"]
    assert storefront == direct
    assert storefront["price_amount"] == "2222.50"
    assert storefront["brand"] == "Persisted brand"
    assert storefront["image_url"] is None
    conv = conversation(client, headers)
    queued = submit(client, conv, headers, text=source.article, key=str(uuid4()))
    assert queued.status_code == 202
    assert asyncio.run(services.worker.run_once())
    result = client.get(f"/api/v1/turns/{queued.json()['data']['turn']['id']}").json()["data"]
    assert result["status"] == "completed"
    assert result["output"]["products"][0] == storefront


@pytest.mark.parametrize("params", [{"page_size": 25}, {"page": 0}, {"sort": "price; DROP TABLE"},
                                    {"min_price": "NaN"}, {"max_price": "Infinity"},
                                    {"min_price": 20, "max_price": 10}, {"category": "'; DROP TABLE--"}])
def test_invalid_browse_parameters_are_rejected(browse, params):
    response = browse[1].get("/api/v1/catalog/products", params=params)
    assert response.status_code == 422


def test_bound_search_and_brand_parameters_cannot_change_sql(browse):
    count = page(browse)["total"]
    assert page(browse, brand="' OR 1=1; DROP TABLE catalog_products;--")["total"] == 0
    page(browse, query="' OR 1=1; DROP TABLE catalog_products;--")
    assert page(browse)["total"] == count


def test_browse_endpoints_require_session(browse):
    with TestClient(create_app(container=browse[0])) as anonymous:
        assert anonymous.get("/api/v1/catalog/categories").status_code == 401
        assert anonymous.get("/api/v1/catalog/products").status_code == 401
