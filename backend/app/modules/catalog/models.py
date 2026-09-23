"""Доменная модель товара.

Это контракт между импортом каталога (участник 2) и логикой ассистента (участник 1).
Импорт заполняет Product из ответа EKT API или синтетического файла, дальше код
ассистента работает только с этими полями.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    # Остаток есть только на складах, откуда не продаём (брак, образцы и т.п.)
    NOT_SELLABLE = "not_sellable"
    # Источник не ответил или поле отсутствует. Это не ноль.
    UNKNOWN = "unknown"


class AttributeStatus(str, Enum):
    OK = "ok"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class StoreStock:
    store_id: int
    name: str
    quantity: int


@dataclass(frozen=True)
class Observation:
    source: str  # "name", "description", "properties.NOMINALNYY_TOK"
    value: str


@dataclass(frozen=True)
class Attribute:
    key: str  # rated_current, poles, voltage, ...
    label: str  # "Номинальный ток"
    value: str | None  # None, если значения противоречат друг другу
    unit: str | None
    status: AttributeStatus
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True)
class Certificate:
    title: str
    number: str | None
    valid_until: str | None
    url: str


@dataclass(frozen=True)
class Stock:
    status: StockStatus
    reported_quantity: int | None  # как отдал источник (поле quantity)
    sellable_quantity: int | None  # только склады, откуда продаём
    stores: tuple[StoreStock, ...] = ()


@dataclass(frozen=True)
class Product:
    id: int
    article: str  # внутренний артикул, как в источнике, с "_" и нулями
    supplier_article: str | None
    name: str
    brand: str | None
    price: Decimal | None  # тенге; None, если цены нет или она 0
    unit: str  # "шт", "м"; количество в корзине целое только для "шт"
    min_order: int | None  # KRATNOST_MIN, если источник его дал
    category_path: tuple[str, ...]
    url: str
    image: str | None
    description: str
    attributes: tuple[Attribute, ...]
    stock: Stock
    certificates: tuple[Certificate, ...]
    fetched_at: datetime
    warnings: tuple[str, ...] = field(default=())
    barcode: str | None = None  # CML2_BAR_CODE

    def attribute(self, key: str) -> Attribute | None:
        return next((a for a in self.attributes if a.key == key), None)

    @property
    def category(self) -> str | None:
        return "/".join(self.category_path) or None
