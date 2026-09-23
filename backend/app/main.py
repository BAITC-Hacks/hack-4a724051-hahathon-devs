import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.responses import success
from app.api.v1.router import router
from app.bootstrap import Container, build_container
from app.contracts import Envelope, ErrorEnvelope
from app.core.config import Settings
from app.core.errors import AppError
from app.core.http import RequestBoundary, error_response


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    services = container if container is not None else build_container(settings or Settings())

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(services.store.initialize)
        try:
            yield
        finally:
            planner = getattr(services.worker.processor, "planner", None)
            if planner is not None:
                await planner.aclose()

    app = FastAPI(
        title="EKT participant 2 API", version="0.1.0", lifespan=lifespan,
        responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)},
    )
    app.state.container = services

    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return error_response(error.code, error.message, error.status, request.state.request_id, error.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error):
        # Validation errors can include the original input. Do not echo it.
        return error_response("validation_error", "Проверьте формат и ограничения полей.", 422, request.state.request_id)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error):
        return error_response("storage_unavailable", "Хранилище временно недоступно.", 503, request.state.request_id, True)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error):
        return error_response("http_error", "Запрос недоступен.", error.status_code, request.state.request_id)

    @app.get("/health/live", response_model=Envelope[dict[str, str]], tags=["system"])
    def live(request: Request):
        return success(request, {"status": "alive"})

    @app.get("/health/ready", response_model=Envelope[dict[str, str]], tags=["system"])
    def ready(request: Request):
        services.store.ping()
        return success(request, {"status": "ready", "mode": "development"})

    app.include_router(router)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=services.settings.allowed_hosts)
    app.add_middleware(
        CORSMiddleware, allow_origins=services.settings.allowed_origins,
        allow_credentials=True, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(RequestBoundary, container=services)
    return app
