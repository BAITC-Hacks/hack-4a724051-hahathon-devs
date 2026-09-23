"""Session revocation and per-session rate limiting.

Before logout existed a session cookie stayed valid for its whole TTL, so a
shared browser kept access to the previous visitor's chat history.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.bootstrap import build_container
from app.main import create_app
from conftest import ORIGIN, authenticate, conversation


def test_logout_requires_csrf_and_origin(client):
    authenticate(client)
    assert client.post("/api/v1/session/logout").status_code == 403
    assert client.post("/api/v1/session/logout", headers={"Origin": ORIGIN}).status_code == 403
    # The cookie still works, so a failed logout never silently signs the client out.
    assert client.get("/api/v1/capabilities").status_code == 200


def test_logout_revokes_server_side_and_drops_history(client, services):
    headers = authenticate(client)
    conv = conversation(client, headers)
    assert client.get(f"/api/v1/conversations/{conv}/messages").status_code == 200

    assert client.post("/api/v1/session/logout", headers=headers).json()["data"] == {"ended": True}

    # Replaying the captured cookie and CSRF token must not resurrect the session.
    assert client.get(f"/api/v1/conversations/{conv}/messages").status_code == 401
    assert client.post("/api/v1/conversations", headers=headers).status_code == 401
    with services.store.connection() as db:
        assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_logout_issues_a_distinct_session(client):
    first = authenticate(client)
    client.post("/api/v1/session/logout", headers=first)
    assert authenticate(client)["X-CSRF-Token"] != first["X-CSRF-Token"]


def test_reads_are_rate_limited_per_session(tmp_path):
    settings = Settings(_env_file=None, app_env="test", requests_per_minute=3,
                        local_db_path=tmp_path / "state.sqlite3")
    with TestClient(create_app(container=build_container(settings))) as client:
        headers = authenticate(client)
        conv = conversation(client, headers)
        statuses = [client.get(f"/api/v1/conversations/{conv}/messages").status_code for _ in range(4)]
    # The session bucket bounds one client even when every request shares a proxy IP.
    assert 429 in statuses
