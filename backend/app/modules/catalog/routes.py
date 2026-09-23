import asyncio
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query, Request

from app.api.v1.dependencies import Services, SessionRead
from app.api.v1.responses import success
from app.contracts import Envelope
from app.core.errors import AppError
from app.modules.catalog.browse import CatalogBrowseReader, CatalogBrowseService
from app.modules.catalog.dto import CatalogPage, CategoriesPage

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _browse(services):
    catalog = getattr(services.catalog, "catalog", None)
    if not isinstance(catalog, CatalogBrowseReader):
        raise AppError("requires_integration", "Каталог пока не подключён.", 503, True)
    return CatalogBrowseService(catalog, source_ref=getattr(services.catalog, "source_ref", "catalog"),
                                demo=getattr(services.catalog, "demo", False))


@router.get("/categories", response_model=Envelope[CategoriesPage])
async def categories(request: Request, services: Services, session: SessionRead):
    return success(request, await asyncio.to_thread(_browse(services).categories))


@router.get("/products", response_model=Envelope[CatalogPage])
async def products(request: Request, services: Services, session: SessionRead,
                   query: str = Query(default="", max_length=256),
                   category: str = Query(default="", max_length=512, pattern=r"^[a-zA-Z0-9_/-]*$"),
                   brand: str = Query(default="", max_length=100), stock_only: bool = False,
                   min_price: Decimal | None = Query(default=None, ge=0, max_digits=18, decimal_places=4),
                   max_price: Decimal | None = Query(default=None, ge=0, max_digits=18, decimal_places=4),
                   sort: Literal["relevance", "price_asc", "price_desc", "name"] = "relevance",
                   page: int = Query(default=1, ge=1, le=1000000),
                   page_size: int = Query(default=12, ge=1, le=24)):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise AppError("invalid_price_range", "Минимальная цена не может превышать максимальную.", 422)
    return success(request, await asyncio.to_thread(
        _browse(services).products, query=query, category=category.strip("/"), brand=brand,
        stock_only=stock_only, min_price=min_price, max_price=max_price, sort=sort, page=page, page_size=page_size,
    ))
