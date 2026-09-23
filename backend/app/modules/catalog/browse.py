"""Read-only storefront browsing over the same catalog used by the assistant."""
import json
from pathlib import Path
from typing import Protocol, runtime_checkable
from app.integrations.domain import product_view
from app.modules.catalog.dto import CatalogPage, CategoriesPage, CategoryNode


# Top-level labels checked against EKT navigation; zero counts remain visible.
CATEGORY_LABELS = {
    "kabel_provod": "Кабель / Провод",
    "svetilniki_lampy": "Светильники / Лампы",
    "nizkovoltnaya_apparatura": "Низковольтная аппаратура",
    "kabelenesushchie_sistemy": "Кабеленесущие системы",
    "izdeliya_dlya_montazha_i_instrument": "Изделия для монтажа и инструмент",
    "prochee_oborudovanie": "Прочее оборудование",
    "shkafy_shchity": "Шкафы / Щиты",
    "rozetki_vyklyuchateli_korobki": "Розетки/Выключатели/Коробки",
    "avtomatizatsiya": "Автоматизация",
    "videonablyudenie_skud_signalizatsiya": "Видеонаблюдение / СКУД / Сигнализация",
    "instrument_kip": "Инструмент / КИП",
    "korzina_elektrika": "Корзина Электрика",
}
SUBCATEGORY_LABELS = {
    "kabel_silovoy": "Кабель силовой",
    "svetilniki_ofisnye": "Светильники офисные",
    "modulnye_avtomaticheskie_vyklyuchateli": "Модульные автоматические выключатели",
    "silovye_avtomaticheskie_vyklyuchateli": "Силовые автоматические выключатели",
    "differentsialnye_avtomaty": "Дифференциальные автоматы",
    "rozetki": "Розетки",
}
# Остальные подразделы: заголовки страниц ekt.kz (data/synthetic/fetch_ekt_selection.py labels).
_LABELS_FILE = Path(__file__).resolve().parents[4] / "data" / "curated" / "category_labels.json"
if _LABELS_FILE.exists():
    for _path, _label in json.loads(_LABELS_FILE.read_text(encoding="utf-8")).items():
        SUBCATEGORY_LABELS.setdefault(_path.rsplit("/", 1)[-1], _label)


@runtime_checkable
class CatalogBrowseReader(Protocol):
    """Read a category snapshot or a consistent filtered page from any database."""
    def category_counts(self) -> list[tuple[tuple[str, ...], int]]: ...

    def browse(self, *, query="", category="", brand="", stock_only=False, min_price=None, max_price=None,
               sort="relevance", page=1, page_size=12): ...


class CatalogBrowseService:
    def __init__(self, catalog: CatalogBrowseReader, *, source_ref="synthetic_catalog", demo=True):
        self.catalog = catalog
        self.source_ref, self.demo = source_ref, demo

    def categories(self) -> CategoriesPage:
        roots = {slug: {"path": [slug], "name": title, "count": 0, "children": {}}
                 for slug, title in CATEGORY_LABELS.items()}
        for path, count in self.catalog.category_counts():
            level = roots
            for index, slug in enumerate(path):
                node = level.setdefault(slug, {"path": list(path[:index + 1]),
                                               "name": CATEGORY_LABELS.get(slug, SUBCATEGORY_LABELS.get(slug, slug)),
                                               "count": 0, "children": {}})
                node["count"] += count
                level = node["children"]
        def convert(node):
            return CategoryNode(path=node["path"], name=node["name"], count=node["count"],
                                children=[convert(child) for child in node["children"].values()])
        return CategoriesPage(items=[convert(node) for node in roots.values()])

    def products(self, **filters) -> CatalogPage:
        items, total, brands = self.catalog.browse(**filters)
        page, size = filters.get("page", 1), filters.get("page_size", 12)
        return CatalogPage(items=[product_view(product, self.source_ref, self.demo) for product in items], total=total,
                           page=page, page_size=size, pages=(total + size - 1) // size, brands=brands,
                           warnings=[*(["synthetic_demo_data"] if self.demo else []),
                                     "catalog_coverage_is_partial_or_unknown"])
