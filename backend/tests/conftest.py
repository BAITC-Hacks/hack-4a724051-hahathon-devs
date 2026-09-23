import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.core.config import Settings
from app.main import create_app

ORIGIN = "http://localhost:3000"


@pytest.fixture
def services(tmp_path):
    return build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "state.sqlite3"))


@pytest.fixture
def client(services):
    with TestClient(create_app(container=services)) as client:
        yield client


def authenticate(client):
    response = client.post("/api/v1/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["data"]["csrf_token"]}


def conversation(client, headers):
    response = client.post("/api/v1/conversations", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def submit(client, conv, headers, text="товар", key="request-0001", **extra):
    return client.post(
        f"/api/v1/conversations/{conv}/turns",
        headers={**headers, "Idempotency-Key": key}, json={"text": text, **extra},
    )


from app.adapters.memory import InMemoryCartStore, InMemoryCatalog, InMemoryProposalStore
from app.modules.actions.service import ActionService


@pytest.fixture
def catalog():
    return InMemoryCatalog.from_file()


@pytest.fixture
def actions(catalog):
    return ActionService(catalog, InMemoryProposalStore(), InMemoryCartStore(), cart_url="/cart")


def by_name(catalog, fragment):
    return next(p for p in catalog._by_id.values() if fragment in p.name)
