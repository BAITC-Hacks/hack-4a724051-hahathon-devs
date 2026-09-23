import json

import httpx
import pytest

from app.adapters.ekt.client import (
    EktAuthError, EktClient, EktNotFound, EktSchemaError, EktUnavailable, MAX_RESPONSE_BYTES,
)


def client(handler):
    return EktClient("u", "p", transport=httpx.MockTransport(handler))


def detail(pid=5):
    return {"id": pid, "name": "Товар", "stores": []}


def test_basic_auth_and_fixed_path():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json=detail())

    assert client(handler).get_detail(5)["id"] == 5
    assert seen["auth"].startswith("Basic ")
    assert seen["url"] == "https://ekt.kz/api/products/detail?id=5"


def test_auth_error_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(EktAuthError):
        client(handler).get_detail(5)
    assert len(calls) == 1


def test_server_error_retried_once_then_unavailable():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(EktUnavailable):
        client(handler).list_page(1)
    assert len(calls) == 2


def test_timeout_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(EktUnavailable):
        client(handler).get_detail(5)


def test_redirect_is_not_followed():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    with pytest.raises(EktSchemaError):
        client(handler).get_detail(5)


def test_404_and_bad_payloads():
    with pytest.raises(EktNotFound):
        client(lambda r: httpx.Response(404)).get_detail(5)
    with pytest.raises(EktSchemaError):
        client(lambda r: httpx.Response(200, content=b"<html>")).get_detail(5)
    with pytest.raises(EktSchemaError):
        client(lambda r: httpx.Response(200, json=detail(pid=6))).get_detail(5)
    with pytest.raises(EktSchemaError):
        client(lambda r: httpx.Response(200, json={"items": [{"no_id": 1}]})).list_page(1)


def test_response_size_limit():
    big = json.dumps({"id": 5, "name": "x" * (MAX_RESPONSE_BYTES + 10)}).encode()
    with pytest.raises(EktSchemaError):
        client(lambda r: httpx.Response(200, content=big)).get_detail(5)


def test_requires_credentials():
    with pytest.raises(ValueError):
        EktClient("", "")
