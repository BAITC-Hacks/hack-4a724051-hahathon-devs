"""ASGI limits apply before JSON parsing, including chunked bodies."""
import hashlib
import asyncio
import hmac
import sqlite3
import time
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.requests import Request

from app.core.errors import AppError
from app.core.security import csrf_token, token_hash


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
        request_headers = Headers(scope=scope)
        origin = request_headers.get("origin")

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                if (origin in self.container.settings.allowed_origins
                        and not any(name.lower() == b"access-control-allow-origin"
                                    for name, _ in headers)):
                    headers.extend([
                        (b"access-control-allow-origin", origin.encode("latin-1")),
                        (b"access-control-allow-credentials", b"true"),
                        (b"vary", b"Origin"),
                    ])
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
            is_upload = scope["path"] == "/api/v1/assets/upload" and scope["method"] == "POST"
            limit = self.container.settings.max_body_bytes
            if is_upload:
                if not self.container.settings.uploads_enabled:
                    return await reject("uploads_disabled", "Загрузка файлов выключена.", 503)
                if origin not in self.container.settings.allowed_origins:
                    return await reject("origin_forbidden", "Источник запроса не разрешён.", 403)
                cookie = Request(scope).cookies.get(self.container.settings.session_cookie, "")
                if not cookie or len(cookie) > 128:
                    return await reject("session_required", "Создайте сессию.", 401)
                session = await run_in_threadpool(self.container.store.find_session, token_hash(cookie), time.time())
                if session is None:
                    return await reject("session_required", "Сессия истекла.", 401)
                if not hmac.compare_digest(request_headers.get("x-csrf-token", ""), csrf_token(cookie)):
                    return await reject("csrf_failed", "Не удалось подтвердить источник действия.", 403)
                limit = self.container.settings.max_upload_bytes
            if scope["path"].startswith("/api/"):
                # Charge before accepting a body, so a limited client cannot keep a
                # connection occupied by slowly streaming an oversized payload.
                peer = scope.get("client")
                ip_hash = hashlib.sha256((peer[0] if peer else "unknown").encode()).hexdigest()
                await run_in_threadpool(
                    self.container.store.consume_rate, "ip:" + ip_hash,
                    self.container.settings.requests_per_minute, time.time(),
                )
            content_length = request_headers.get("content-length", "")
            if content_length.isdecimal():
                declared = content_length.lstrip("0") or "0"
                maximum = str(limit)
                if len(declared) > len(maximum) or (len(declared) == len(maximum)
                                                     and declared > maximum):
                    return await reject("request_too_large", "Слишком большой запрос.", 413)
            chunks, total = [], 0
            while True:
                message = await asyncio.wait_for(receive(), timeout=15)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    return await reject("request_too_large", "Слишком большой запрос.", 413)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            if body and scope["method"] in {"POST", "PUT", "PATCH"} and not is_upload:
                if request_headers.get("content-type", "").split(";")[0].strip() != "application/json":
                    return await reject("unsupported_media_type", "Ожидается application/json.", 415)
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await self.app(scope, replay, secured_send)
        except TimeoutError:
            if started:
                raise
            await reject("request_timeout", "Истекло время приёма запроса.", 408)
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
