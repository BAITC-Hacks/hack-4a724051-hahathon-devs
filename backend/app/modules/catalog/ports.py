"""Порты каталога. Реализует участник 2 (БД + EKT API), использует логика ассистента."""

from typing import Protocol

from app.modules.catalog.models import Product


class CatalogReader(Protocol):
    def get_product(self, product_id: int, fresh: bool = False) -> Product | None:
        """Товар по внутреннему ID.

        fresh=True означает перечитать цену и остатки из источника прямо сейчас
        (используется перед добавлением в корзину). Если источник недоступен,
        вернуть товар со stock.status=UNKNOWN, а не None и не нулевой остаток.
        """

    def find_by_identifier(self, value: str) -> list[Product]:
        """Точное совпадение по article, supplier_article или штрихкоду. Регистр не важен."""

    def search_text(self, query: str, limit: int = 20) -> list[Product]:
        """Полнотекстовый поиск по name/description, самые релевантные первыми."""

    def list_category(self, category_path: tuple[str, ...], limit: int = 200) -> list[Product]:
        """Товары той же категории (для подбора аналогов)."""
