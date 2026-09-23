"""ASGI limits apply before JSON parsing, including chunked bodies."""
import hashlib
import sqlite3
import time
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.core.errors import AppError


def error_response(code: str, message: str, status: int, request_id: str, retryable: bool = False):
    headers = {"Retry-After": "60"} if status == 429 else {}
    return JSONResponse(
        {"error": {"code": code, "message": message, "retryable": retryable},
         "meta": {"request_id": request_id, "warnings": []}},
        status_code=status, headers=headers,
    )


class RequestBoundary:
    def __init__(self, app, container):
        self.app, self.container = app, container

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                ])
                if scope["path"] not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
                    headers.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                message = {**message, "headers": headers}
            await send(message)

        async def reject(code, message, status, retryable=False):
            response = error_response(code, message, status, request_id, retryable)
            await response(scope, receive, secured_send)

        try:
            chunks, total = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > self.container.settings.max_body_bytes:
                    return await reject("request_too_large", "Слишком большой запрос.", 413)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            headers = dict(scope.get("headers", []))
            if body and scope["method"] in {"POST", "PUT", "PATCH"}:
                if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
                    return await reject("unsupported_media_type", "Ожидается application/json.", 415)
            if scope["path"].startswith("/api/"):
                # Never trust arbitrary X-Forwarded-For. Configure the server proxy boundary explicitly.
                peer = scope.get("client")
                ip_hash = hashlib.sha256((peer[0] if peer else "unknown").encode()).hexdigest()
                await run_in_threadpool(
                    self.container.store.consume_rate, "ip:" + ip_hash,
                    self.container.settings.requests_per_minute, time.time(),
                )
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, secured_send)
        except AppError as error:
            if started:
                raise
            await reject(error.code, error.message, error.status, error.retryable)
        except sqlite3.Error:
            if started:
                raise
            await reject("storage_unavailable", "Хранилище временно недоступно.", 503, True)
        except Exception:
            if started:
                raise
            await reject("internal_error", "Не удалось обработать запрос.", 500)
