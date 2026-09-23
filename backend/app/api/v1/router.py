import asyncio
import time
import re
import html
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

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
from app.modules.catalog.routes import router as catalog_router
from app.modules.documents.models import AssetView

router = APIRouter(prefix="/api/v1")
router.include_router(chat_router)
router.include_router(catalog_router)


@router.post("/assets/upload", status_code=201, response_model=Envelope[AssetView], tags=["documents"],
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
             }, "description": "Raw file bytes; set the actual MIME type matching filename, not multipart."}})
async def upload_asset(request: Request, services: Services, session: SessionWrite,
                       filename: str = Query(min_length=1, max_length=255)):
    if not services.settings.uploads_enabled or not hasattr(services.documents, "upload"):
        raise AppError("uploads_disabled", "Загрузка файлов выключена.", 503)
    return success(request, await services.documents.upload(session.id, filename,
                   request.headers.get("content-type", ""), await request.body()))


@router.get("/assets/{asset_id}", response_model=Envelope[AssetView], tags=["documents"])
async def asset_status(asset_id: UUID, request: Request, services: Services, session: SessionRead):
    if not hasattr(services.documents, "get"):
        raise AppError("uploads_disabled", "Загрузка файлов выключена.", 503)
    return success(request, await services.documents.get(session.id, str(asset_id)))


@router.post("/assets/{asset_id}/delete", tags=["documents"])
async def delete_asset(asset_id: UUID, request: Request, services: Services, session: SessionWrite):
    if not hasattr(services.documents, "delete"):
        raise AppError("uploads_disabled", "Загрузка файлов выключена.", 503)
    await services.documents.delete(session.id, str(asset_id))
    return success(request, {"deleted": True})


@router.get("/certificates/{name}", tags=["catalog"])
def certificate(name: str, services: Services, session: SessionRead):
    if services.settings.integration_mode != "synthetic" or not re.fullmatch(r"SYN-[0-9]+\.pdf", name):
        raise AppError("certificate_not_found", "Сертификат не найден.", 404)
    path = Path(__file__).resolve().parents[4] / "data" / "synthetic" / "certificates" / name
    if not path.is_file():
        raise AppError("certificate_not_found", "Сертификат не найден.", 404)
    return FileResponse(path, media_type="application/pdf", filename=name)


@router.get("/product-images/{name}", tags=["catalog"])
def product_image(name: str, services: Services):
    """Демо-иллюстрации синтетического каталога. Без сессии: картинка может грузиться до её создания."""
    if services.settings.integration_mode not in {"synthetic", "catalog_db"} or not re.fullmatch(r"SYN-[0-9]+\.svg", name):
        raise AppError("image_not_found", "Изображение не найдено.", 404)
    path = Path(__file__).resolve().parents[4] / "data" / "synthetic" / "images" / name
    if not path.is_file():
        raise AppError("image_not_found", "Изображение не найдено.", 404)
    # SVG без скриптов, но на случай прямого открытия запрещаем всё, кроме встроенных стилей.
    return FileResponse(path, media_type="image/svg+xml", headers={
        "Cache-Control": "public, max-age=86400",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


@router.get("/cart/view", response_class=HTMLResponse, tags=["actions"])
async def cart_page(services: Services, session: SessionRead):
    cart = await bounded_read(services.actions.get_cart(session.id))
    lines = "".join(f"<tr><td>{html.escape(i.article_original)} — {html.escape(i.name)}</td><td>{i.quantity}</td><td>{html.escape(i.line_total_amount)}</td></tr>"
                    for i in cart.items)
    return HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                        '<title>Корзина ЭКТ</title><body><h1>Корзина</h1>'
                        + ('<p>Демонстрационная корзина. Реальный заказ не оформляется.</p>' if cart.mode == "demo" else '')
                        + '<table><tr><th>Товар</th><th>Количество</th><th>Сумма</th></tr>' + lines + '</table>'
                        + f'<p>Итого: {html.escape(cart.total_amount)} {html.escape(cart.currency)}</p></body></html>')


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
