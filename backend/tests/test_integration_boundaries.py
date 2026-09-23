from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.contracts import ParsedDocument
from app.core.config import Settings
from app.integrations.unavailable import UnavailableActions
from app.main import create_app
from conftest import authenticate, conversation, submit


def test_idempotent_replay_survives_document_service_outage(tmp_path):
    class Documents:
        calls = 0

        async def resolve(self, session_id, asset_ids):
            self.calls += 1
            assert session_id
            if self.calls > 1:
                raise RuntimeError("Document service is now offline")
            return [ParsedDocument(asset_id=asset, status="ready", text="text") for asset in asset_ids]

    documents = Documents()
    services = build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "db.sqlite3"),
                               documents=documents)
    with TestClient(create_app(container=services)) as client:
        headers = authenticate(client)
        conv = conversation(client, headers)
        assets = [str(uuid4())]
        first = submit(client, conv, headers, asset_ids=assets)
        assert first.status_code == 202
        replay = submit(client, conv, headers, asset_ids=assets)
        assert replay.status_code == 202
        assert replay.json()["data"]["created"] is False
        assert documents.calls == 1
        changed = submit(client, conv, headers, asset_ids=[str(uuid4())])
        assert changed.status_code == 409


def test_confirmation_passes_server_identity_and_reports_unknown_outcome(tmp_path):
    class Actions(UnavailableActions):
        received = None

        async def confirm(self, session_id, proposal_id, version, key):
            self.received = (session_id, proposal_id, version, key)
            raise TimeoutError()

    actions = Actions()
    services = build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "db.sqlite3"),
                               actions=actions)
    with TestClient(create_app(container=services)) as client:
        headers = authenticate(client)
        proposal_id = str(uuid4())
        response = client.post(f"/api/v1/proposals/{proposal_id}/confirm", json={"version": 2},
                               headers={**headers, "Idempotency-Key": "confirm-0001"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "action_outcome_unknown"
        assert response.json()["error"]["retryable"] is False
        assert actions.received[0]
        assert actions.received[1:] == (proposal_id, 2, "confirm-0001")


def test_chunked_request_cannot_bypass_body_limit(client):
    response = client.post("/api/v1/session", content=iter([b"x" * 20000, b"y" * 20000]),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_unknown_exception_does_not_expose_content(tmp_path):
    class Catalog:
        async def search(self, query, limit):
            raise RuntimeError("API-KEY-SECRET")

    services = build_container(Settings(_env_file=None, app_env="test", local_db_path=tmp_path / "db.sqlite3"),
                               catalog=Catalog())
    with TestClient(create_app(container=services)) as client:
        authenticate(client)
        response = client.get("/api/v1/products", params={"query": "x"})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "SECRET" not in response.text
