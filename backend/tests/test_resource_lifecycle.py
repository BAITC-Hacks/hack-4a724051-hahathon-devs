"""Lifecycle checks use local fakes; they never connect to PostgreSQL or EKT."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.core.config import Settings
from app.main import create_app
from app.worker import run_container


def database_container(monkeypatch, tmp_path, events, *, coverage_fails=False):
    from app.adapters.ekt import client
    from app.infrastructure import db
    from app.modules.catalog import sync

    class Pool:
        def close(self):
            events.append("pool_closed")

    class Live:
        def __init__(self, *args):
            events.append("live_opened")

        def close(self):
            events.append("live_closed")

    def coverage(pool):
        if coverage_fails:
            raise RuntimeError("catalog bootstrap failed")
        return {"products": {"synthetic": 65}, "coverage": "partial"}

    monkeypatch.setattr(db, "create_pool", lambda url: Pool())
    monkeypatch.setattr(client, "EktClient", Live)
    monkeypatch.setattr(sync, "coverage", coverage)
    monkeypatch.setattr(sync, "prepare_catalog", lambda *args: None)  # фейковый пул, без миграций
    return build_container(Settings(_env_file=None, app_env="test", integration_mode="catalog_db",
                                    database_url="postgresql://unused", ekt_live_refresh=True,
                                    ekt_api_user="test-user", ekt_api_password="test-password",
                                    local_db_path=tmp_path / "state.sqlite3"))


def test_api_shutdown_closes_all_owned_resources_once(monkeypatch, tmp_path):
    events = []
    services = database_container(monkeypatch, tmp_path, events)

    class Planner:
        async def aclose(self):
            events.append("planner_closed")

    services.worker.processor.planner = Planner()
    with TestClient(create_app(container=services)):
        assert events == ["live_opened"]
    asyncio.run(services.aclose())
    assert events == ["live_opened", "planner_closed", "live_closed", "pool_closed"]


def test_catalog_partial_construction_failure_closes_pool_and_client(monkeypatch, tmp_path):
    events = []
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        database_container(monkeypatch, tmp_path, events, coverage_fails=True)
    assert events == ["live_opened", "live_closed", "pool_closed"]


def test_api_initialization_failure_still_closes_resources(monkeypatch, tmp_path):
    events = []
    services = database_container(monkeypatch, tmp_path, events)

    def fail():
        raise RuntimeError("initialize failed")

    monkeypatch.setattr(services.store, "initialize", fail)
    with pytest.raises(RuntimeError, match="initialize failed"):
        with TestClient(create_app(container=services)):
            pass
    assert events[-2:] == ["live_closed", "pool_closed"]


def test_planner_close_failure_does_not_leak_database_resources(monkeypatch, tmp_path):
    events = []
    services = database_container(monkeypatch, tmp_path, events)

    class Planner:
        async def aclose(self):
            raise RuntimeError("planner close failed")

    services.worker.processor.planner = Planner()
    with pytest.raises(RuntimeError, match="planner close failed"):
        asyncio.run(services.aclose())
    assert events[-2:] == ["live_closed", "pool_closed"]


@pytest.mark.parametrize("stage", ["initialize", "once", "cancelled"])
def test_worker_cleanup_runs_on_failed_initialization_execution_or_cancellation(monkeypatch, tmp_path, stage):
    events = []
    services = database_container(monkeypatch, tmp_path, events)

    def initialize():
        if stage == "initialize":
            raise RuntimeError("initialize failed")

    async def execute():
        if stage == "cancelled":
            raise asyncio.CancelledError()
        raise RuntimeError("execution failed")

    monkeypatch.setattr(services.store, "initialize", initialize)
    monkeypatch.setattr(services.worker, "run_once", execute)
    monkeypatch.setattr(services.worker, "run_forever", execute)
    with pytest.raises(asyncio.CancelledError if stage == "cancelled" else RuntimeError):
        asyncio.run(run_container(services, once=stage != "cancelled"))
    assert events[-2:] == ["live_closed", "pool_closed"]


def test_pool_open_timeout_closes_partially_created_pool(monkeypatch):
    from app.infrastructure import db
    events = []

    class Pool:
        def __init__(self, *args, **kwargs):
            pass

        def open(self, **kwargs):
            raise TimeoutError("test timeout")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(db, "ConnectionPool", Pool)
    with pytest.raises(TimeoutError):
        db.create_pool("postgresql://unused")
    assert events == ["closed"]


def test_readiness_fails_closed_when_postgres_catalog_is_unavailable(tmp_path):
    def ping():
        raise RuntimeError("private-database-details")

    services = build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "state.sqlite3"),
                               catalog=SimpleNamespace(catalog=SimpleNamespace(ping=ping)))
    with TestClient(create_app(container=services)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert "private-database-details" not in response.text
