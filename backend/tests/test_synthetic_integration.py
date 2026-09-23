"""Exercise participant-1 domain logic through the durable participant-2 API."""
import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.contracts import CartItem, ProposalInput
from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app
from app.modules.catalog.models import StockStatus
from conftest import authenticate, conversation, submit


@pytest.fixture
def demo(tmp_path):
    services = build_container(Settings(_env_file=None, app_env="test", integration_mode="synthetic",
                                        local_db_path=tmp_path / "demo.sqlite3"))
    services.store.initialize()
    return services


def session(demo, token="test-token"):
    return demo.store.create_session(token, time.time() + 3600).id


def proposal(demo, sid, key="proposal-0001"):
    return asyncio.run(demo.actions.propose(sid, ProposalInput(items=[CartItem(product_id=900001, quantity=2)]), key))


def test_catalog_worker_and_confirmed_cart_via_http(demo):
    with TestClient(create_app(container=demo)) as client:
        headers = authenticate(client)
        catalog = client.get("/api/v1/products", params={"query": "990100001_"})
        assert catalog.status_code == 200
        item = catalog.json()["data"]["items"][0]
        assert item["article_original"] == "990100001_"
        assert "synthetic_demo_data" in item["warnings"]
        conv = conversation(client, headers)
        queued = submit(client, conv, headers, text="990100001_")
        assert queued.status_code == 202
        assert asyncio.run(demo.worker.run_once())
        turn_id = queued.json()["data"]["turn"]["id"]
        turn = client.get(f"/api/v1/turns/{turn_id}").json()["data"]
        assert turn["status"] == "completed"
        assert turn["output"]["products"][0]["id"] == item["id"]
        prepared = client.post("/api/v1/cart/proposals", headers={**headers, "Idempotency-Key": "proposal-0001"},
                               json={"items": [{"product_id": item["id"], "quantity": 2}]})
        assert prepared.status_code == 202, prepared.text
        draft = prepared.json()["data"]
        assert draft["mode"] == "demo"
        assert client.get("/api/v1/cart").json()["data"]["items"] == []
        for _ in range(2):
            applied = client.post(f"/api/v1/proposals/{draft['id']}/confirm", json={"version": draft["version"]},
                                  headers={**headers, "Idempotency-Key": "confirm-0001"})
            assert applied.status_code == 202, applied.text
            assert applied.json()["data"]["status"] == "applied"
        assert client.get("/api/v1/cart").json()["data"]["items"][0]["quantity"] == 2


def test_durable_parallel_confirmation_and_request_binding(demo):
    sid = session(demo)
    draft = proposal(demo, sid)
    restarted = build_container(demo.settings)
    def confirm(index):
        return asyncio.run(restarted.actions.confirm(sid, str(draft.id), 1, f"confirm-{index:04}"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(p.status == "applied" for p in pool.map(confirm, range(6)))
    cart = asyncio.run(demo.actions.get_cart(sid))
    assert cart.items[0].quantity == 2
    assert cart.version == 2
    assert proposal(restarted, sid).id == draft.id
    with pytest.raises(AppError, match="Ключ") as error:
        asyncio.run(demo.actions.propose(sid, ProposalInput(items=[CartItem(product_id=900001, quantity=3)]),
                                         "proposal-0001"))
    assert error.value.status == 409


def test_proposal_ownership_version_rejection_and_expired_session(demo):
    sid, other = session(demo), session(demo, "other-token")
    draft = proposal(demo, sid)
    for method, args in [(demo.actions.get_proposal, (other, str(draft.id))),
                         (demo.actions.confirm, (other, str(draft.id), 1, "confirm-0001")),
                         (demo.actions.reject, (other, str(draft.id)))]:
        with pytest.raises(AppError) as error:
            asyncio.run(method(*args))
        assert error.value.status == 404
    with pytest.raises(AppError) as error:
        asyncio.run(demo.actions.confirm(sid, str(draft.id), 2, "confirm-0002"))
    assert error.value.status == 409
    assert asyncio.run(demo.actions.reject(sid, str(draft.id))).status == "rejected"
    assert asyncio.run(demo.actions.confirm(sid, str(draft.id), 1, "confirm-0003")).status == "rejected"
    assert not asyncio.run(demo.actions.get_cart(sid)).items
    demo.store.cleanup(time.time() + 7200)
    with pytest.raises(AppError) as error:
        asyncio.run(demo.actions.get_cart(sid))
    assert error.value.status == 401
    with sqlite3.connect(demo.settings.local_db_path) as db:
        assert db.execute("SELECT count(*) FROM demo_proposals").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["price", "unknown_stock", "low_stock"])
def test_confirmation_rechecks_current_catalog(demo, change):
    sid = session(demo)
    draft = proposal(demo, sid)
    catalog = demo.catalog.catalog
    product = catalog.get_product(900001)
    if change == "price":
        product = replace(product, price=product.price + Decimal(1))
    elif change == "unknown_stock":
        product = replace(product, stock=replace(product.stock, status=StockStatus.UNKNOWN, sellable_quantity=None))
    else:
        product = replace(product, stock=replace(product.stock, sellable_quantity=1))
    catalog.replace_product(product)
    result = asyncio.run(demo.actions.confirm(sid, str(draft.id), 1, "confirm-0001"))
    assert result.status == "stale"
    assert not asyncio.run(demo.actions.get_cart(sid)).items


def test_proposal_ttl_and_cart_version_are_checked(demo):
    sid = session(demo)
    draft = proposal(demo, sid)
    with sqlite3.connect(demo.settings.local_db_path) as db:
        db.execute("UPDATE demo_proposals SET payload=json_set(payload, '$.expires_at', ?) WHERE id=?",
                   ("2000-01-01T00:00:00+00:00", draft.id.hex))
    assert asyncio.run(demo.actions.confirm(sid, str(draft.id), 1, "confirm-0001")).status == "expired"
    draft = proposal(demo, sid, "proposal-0002")
    with sqlite3.connect(demo.settings.local_db_path) as db:
        db.execute("INSERT INTO demo_carts VALUES (?, ?, ?)", (sid, 1, "{}"))
    assert asyncio.run(demo.actions.confirm(sid, str(draft.id), 1, "confirm-0002")).status == "stale"
