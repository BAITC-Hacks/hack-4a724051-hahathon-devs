import asyncio
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.v1.dependencies import (
    IdempotencyKey, Services, SessionRead, SessionWrite, require_origin,
)
from app.api.v1.responses import success
from app.contracts import (
    CartSnapshot, ConfirmationInput, Envelope, Product, ProposalInput, ProposalView, SearchResult,
)
from app.core.errors import AppError
from app.core.security import csrf_token, new_token, token_hash
from app.modules.chat.models import SessionView
from app.modules.chat.routes import router as chat_router

router = APIRouter(prefix="/api/v1")
router.include_router(chat_router)


async def bounded_read(awaitable):
    try:
        async with asyncio.timeout(5):
            return await awaitable
    except TimeoutError:
        raise AppError("upstream_unavailable", "Источник данных временно недоступен.", 503, True)


async def bounded_action(awaitable):
    try:
        async with asyncio.timeout(5):
            return await awaitable
    except TimeoutError:
        raise AppError(
            "action_outcome_unknown", "Результат действия пока неизвестен. Проверьте его статус.", 503,
        )


@router.post("/session", response_model=Envelope[SessionView], tags=["session"],
             dependencies=[Depends(require_origin)])
def start_session(request: Request, response: Response, services: Services):
    cookie = request.cookies.get(services.settings.session_cookie, "")
    session = services.store.find_session(token_hash(cookie), time.time()) if cookie and len(cookie) <= 128 else None
    if session is None:
        cookie = new_token()
        session = services.store.create_session(
            token_hash(cookie), time.time() + services.settings.session_ttl_seconds,
        )
        response.set_cookie(
            services.settings.session_cookie, cookie, httponly=True,
            secure=services.settings.cookie_secure, samesite="lax", path="/",
            max_age=services.settings.session_ttl_seconds,
        )
    return success(request, SessionView(csrf_token=csrf_token(cookie), expires_at=session.expires_at))


@router.get("/capabilities", response_model=Envelope[dict[str, str]], tags=["system"])
def capabilities(request: Request, services: Services):
    return success(request, services.capabilities)


@router.get("/products", response_model=Envelope[SearchResult], tags=["catalog"])
async def products(request: Request, services: Services, session: SessionRead,
                   query: str = Query(min_length=1, max_length=256), limit: int = Query(default=10, ge=1, le=20)):
    return success(request, await bounded_read(services.catalog.search(query, limit)))


@router.get("/products/{product_id}", response_model=Envelope[Product], tags=["catalog"])
async def product(product_id: int, request: Request, services: Services, session: SessionRead):
    if product_id <= 0:
        raise AppError("invalid_product_id", "Некорректный ID товара.", 422)
    return success(request, await bounded_read(services.catalog.get_product(product_id)))


@router.get("/products/{product_id}/alternatives", response_model=Envelope[SearchResult], tags=["catalog"])
async def alternatives(product_id: int, request: Request, services: Services, session: SessionRead):
    if product_id <= 0:
        raise AppError("invalid_product_id", "Некорректный ID товара.", 422)
    return success(request, await bounded_read(services.catalog.alternatives(product_id)))


@router.get("/cart", response_model=Envelope[CartSnapshot], tags=["actions"])
async def get_cart(request: Request, services: Services, session: SessionRead):
    return success(request, await bounded_read(services.actions.get_cart(session.id)))


@router.post("/cart/proposals", status_code=202, response_model=Envelope[ProposalView], tags=["actions"])
async def propose(payload: ProposalInput, request: Request, services: Services,
                  session: SessionWrite, key: IdempotencyKey):
    # A draft cannot change the cart. The injected application port persists it atomically.
    return success(request, await bounded_action(services.actions.propose(session.id, payload, key)))


@router.get("/proposals/{proposal_id}", response_model=Envelope[ProposalView], tags=["actions"])
async def get_proposal(proposal_id: UUID, request: Request, services: Services, session: SessionRead):
    return success(request, await bounded_read(services.actions.get_proposal(session.id, str(proposal_id))))


@router.post("/proposals/{proposal_id}/confirm", status_code=202,
             response_model=Envelope[ProposalView], tags=["actions"])
async def confirm(proposal_id: UUID, payload: ConfirmationInput, request: Request,
                  services: Services, session: SessionWrite, key: IdempotencyKey):
    # Enqueue only. Adapter MUST handle unknown external outcomes without blind retry.
    return success(request, await bounded_action(services.actions.confirm(session.id, str(proposal_id), payload.version, key)))


@router.post("/proposals/{proposal_id}/reject", response_model=Envelope[ProposalView], tags=["actions"])
async def reject(proposal_id: UUID, request: Request, services: Services, session: SessionWrite):
    return success(request, await bounded_action(services.actions.reject(session.id, str(proposal_id))))
