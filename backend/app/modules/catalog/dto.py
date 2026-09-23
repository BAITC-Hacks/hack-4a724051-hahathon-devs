"""Storefront browse contract; the assistant uses the same Product DTO."""
from typing import Literal

from pydantic import Field

from app.contracts import DTO, Product


class CategoryNode(DTO):
    path: list[str]
    name: str
    count: int = Field(ge=0)
    children: list["CategoryNode"] = Field(default_factory=list)


class CategoriesPage(DTO):
    items: list[CategoryNode]
    coverage: Literal["partial"] = "partial"


class CatalogPage(DTO):
    items: list[Product]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=24)
    pages: int = Field(ge=0)
    coverage: Literal["partial"] = "partial"
    brands: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
