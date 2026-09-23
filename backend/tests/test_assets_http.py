"""HTTP upload boundaries and ownership, with a test-only scanner double."""
import sys

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.core.config import Settings
from app.main import create_app
from conftest import authenticate


@pytest.fixture
def assets(tmp_path):
    scanner = tmp_path / "scanner_double.py"
    scanner.write_text("raise SystemExit(0)", encoding="utf-8")
    settings = Settings(_env_file=None, app_env="test", uploads_enabled=True,
                        scanner_command=[sys.executable, str(scanner)], assets_root=tmp_path / "assets",
                        local_db_path=tmp_path / "state.sqlite3")
    services = build_container(settings)
    with TestClient(create_app(container=services)) as client:
        yield client, services


def test_http_upload_requires_session_csrf_and_ownership(assets):
    client, services = assets
    url = "/api/v1/assets/upload?filename=parts.txt"
    assert client.post(url, content=b"one", headers={"Origin": "http://localhost:3000"}).status_code == 401
    headers = authenticate(client)
    response = client.post(url, content="Кабель 990100001_".encode(), headers={**headers, "Content-Type": "text/plain"})
    assert response.status_code == 201, response.text
    asset = response.json()["data"]
    assert asset["status"] == "ready"
    with TestClient(create_app(container=services)) as stranger:
        other = authenticate(stranger)
        assert stranger.get(f"/api/v1/assets/{asset['id']}").status_code == 404
        assert stranger.post(f"/api/v1/assets/{asset['id']}/delete", headers=other).status_code == 404
    assert client.post(f"/api/v1/assets/{asset['id']}/delete", headers=headers).status_code == 200
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404


def test_upload_limits_and_disabled_scanner(assets):
    client, _ = assets
    headers = authenticate(client)
    url = "/api/v1/assets/upload?filename=parts.txt"
    assert client.post(url, content=b"x", headers={**headers, "Content-Length": "10485761"}).status_code == 413
    bad = {**headers, "X-CSRF-Token": "incorrect", "Content-Type": "text/plain"}
    assert client.post(url, content=b"x", headers=bad).status_code == 403
    assert client.post(url, content=b"x", headers={**headers, "Content-Type": "application/pdf"}).status_code == 415
