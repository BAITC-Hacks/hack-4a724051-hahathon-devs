import hmac
import time
from typing import Annotated

from fastapi import Depends, Header, Request

from app.bootstrap import Container
from app.core.errors import AppError
from app.core.security import csrf_token, token_hash
from app.modules.chat.models import Session


def container(request: Request) -> Container:
    return request.app.state.container


def require_origin(request: Request, services: Annotated[Container, Depends(container)]):
    if request.headers.get("origin") not in services.settings.allowed_origins:
        raise AppError("origin_forbidden", "Источник запроса не разрешён.", 403)


def current_session(request: Request, services: Annotated[Container, Depends(container)]) -> Session:
    token = request.cookies.get(services.settings.session_cookie, "")
    if not token or len(token) > 128:
        raise AppError("session_required", "Создайте сессию перед запросом.", 401)
    session = services.store.find_session(token_hash(token), time.time())
    if session is None:
        raise AppError("session_required", "Сессия истекла. Создайте новую.", 401)
    return session


def mutation_session(
    request: Request,
    services: Annotated[Container, Depends(container)],
    session: Annotated[Session, Depends(current_session)],
    _: Annotated[None, Depends(require_origin)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Session:
    cookie = request.cookies.get(services.settings.session_cookie, "")
    if x_csrf_token is None or not hmac.compare_digest(x_csrf_token, csrf_token(cookie)):
        raise AppError("csrf_failed", "Не удалось подтвердить источник действия.", 403)
    services.store.consume_rate("session:" + session.id, services.settings.requests_per_minute, time.time())
    return session


def idempotency_key(
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
) -> str:
    return key


Services = Annotated[Container, Depends(container)]
SessionRead = Annotated[Session, Depends(current_session)]
SessionWrite = Annotated[Session, Depends(mutation_session)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
