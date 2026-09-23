from app.adapters.memory import InMemoryCatalog
from app.modules.catalog.models import AttributeStatus, StockStatus
from app.modules.catalog.quality import category_from_url, product_from_source, sellable_stock


def raw(**over):
    base = {
        "id": 1, "name": "123 АВ ВА47-29 1P 16А C 4,5кА IEK", "article": "0990001_",
        "description": "Основные характеристики:\r\n\tНоминальный ток: 16А\r\n\tКоличество полюсов: 1",
        "price": 1300, "quantity": 5, "url": "https://ekt.kz/catalog/a/b/slug/", "image": None, "offers": [],
        "stores": [{"id": 13, "name": "Алматы", "quantity": 5}],
        "properties": {"NOMINALNYY_TOK": "16А", "KOLICHESTVO_POLYUSOV": "1", "KRATNOST_MIN": "1"},
    }
    base.update(over)
    return base


def test_article_kept_as_is():
    p = product_from_source(raw())
    assert p.article == "0990001_"
    assert p.category_path == ("a", "b")


def test_conflict_between_description_and_properties():
    p = product_from_source(raw(properties={"NOMINALNYY_TOK": "250 А"},
                                description="Номинальный ток: 160А", name="АВ 3ф 160А"))
    attr = p.attribute("rated_current")
    assert attr.status is AttributeStatus.CONFLICT
    assert attr.value is None
    assert "conflict:rated_current" in p.warnings


def test_one_pole_plus_neutral_is_two_poles():
    p = product_from_source(raw(name="Диф.авт. 1P+N 16А", description="",
                                properties={"KOLICHESTVO_POLYUSOV": "2"}))
    assert p.attribute("poles").status is AttributeStatus.OK


def test_defect_store_is_not_sellable():
    stock = sellable_stock([{"id": 2, "name": "Брак MEGALIGHT", "quantity": 4}], 4)
    assert stock.status is StockStatus.NOT_SELLABLE
    assert stock.sellable_quantity == 0


def test_missing_stock_is_unknown_not_zero():
    p = product_from_source(raw(stores=None, quantity=None))
    assert p.stock.status is StockStatus.UNKNOWN
    assert p.stock.sellable_quantity is None


def test_zero_price_is_missing():
    assert product_from_source(raw(price=0)).price is None


def test_relative_url_category():
    assert category_from_url("/catalog/x/y/item/") == ("x", "y")
    assert category_from_url("https://ekt.kz/news/") == ()


def test_synthetic_catalog_loads():
    catalog = InMemoryCatalog.from_file()
    products = list(catalog._by_id.values())
    assert len(products) > 50
    conflicts = [p for p in products if p.warnings]
    assert [p.name for p in conflicts] == ["971300 АВ DRX250 MT 3ф 160А 18kA Legrand"]
