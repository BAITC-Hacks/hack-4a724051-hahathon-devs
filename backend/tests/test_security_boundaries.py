import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.http import RequestBoundary
from app.main import create_app


ORIGIN = "http://localhost:3000"


def run_boundary(*, headers=(), chunks=(), limited=False):
    calls = {"received": 0, "handled": 0, "rated": 0}
    output = []

    class Store:
        def consume_rate(self, bucket, limit, now):
            calls["rated"] += 1
            assert bucket == "ip:" + hashlib.sha256(b"198.51.100.5").hexdigest()
            if limited:
                raise AppError("rate_limited", "Слишком много запросов.", 429, True)

    async def app(scope, receive, send):
        calls["handled"] += 1
        raise AssertionError("A rejected request reached the application")

    stream = iter(chunks)

    async def receive():
        calls["received"] += 1
        return next(stream)

    async def send(message):
        output.append(message)

    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/session",
        "headers": list(headers), "client": ("198.51.100.5", 1234),
    }
    settings = SimpleNamespace(max_body_bytes=32768, requests_per_minute=2,
                               allowed_origins=[ORIGIN])
    boundary = RequestBoundary(app, SimpleNamespace(settings=settings, store=Store()))
    asyncio.run(boundary(scope, receive, send))
    return calls, output


def response_headers(output):
    start = next(message for message in output if message["type"] == "http.response.start")
    return start["status"], dict(start["headers"])


def test_rate_limit_rejects_before_reading_body_and_preserves_cors():
    calls, output = run_boundary(
        headers=[(b"origin", ORIGIN.encode()), (b"x-forwarded-for", b"203.0.113.9")],
        limited=True,
    )
    status, headers = response_headers(output)
    assert calls == {"received": 0, "handled": 0, "rated": 1}
    assert status == 429
    assert headers[b"access-control-allow-origin"] == ORIGIN.encode()
    assert headers[b"access-control-allow-credentials"] == b"true"
    assert headers[b"retry-after"] == b"60"


def test_declared_oversize_rejects_before_reading_and_chunked_oversize_stops():
    calls, output = run_boundary(headers=[(b"content-length", b"32769")])
    assert calls == {"received": 0, "handled": 0, "rated": 1}
    assert response_headers(output)[0] == 413

    calls, output = run_boundary(chunks=[
        {"type": "http.request", "body": b"x" * 20000, "more_body": True},
        {"type": "http.request", "body": b"x" * 12769, "more_body": False},
    ])
    assert calls == {"received": 2, "handled": 0, "rated": 1}
    assert response_headers(output)[0] == 413


def test_boundary_does_not_expose_cors_to_untrusted_origin():
    _, output = run_boundary(headers=[(b"origin", b"https://evil.example")], limited=True)
    assert b"access-control-allow-origin" not in response_headers(output)[1]


def test_unexpected_error_has_safe_envelope_and_cors(services):
    app = create_app(container=services)

    def fail():
        raise RuntimeError("private detail")

    app.add_api_route("/api/v1/fault", fail, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/fault", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private detail" not in response.text
    assert response.json()["meta"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_session_does_not_adopt_unknown_cookie(client):
    client.cookies.set("ekt_session", "attacker-chosen-value", domain="testserver.local")
    response = client.post("/api/v1/session", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert client.cookies.get("ekt_session") != "attacker-chosen-value"
    assert "attacker-chosen-value" not in response.text


def test_wildcard_host_is_not_accepted():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, allowed_hosts=["*"])
