from app.modules.catalog.models import StockStatus
from app.modules.search.alternatives import AlternativeService
from app.modules.search.service import MatchKind, SearchService
from tests.conftest import by_name


def test_exact_article(catalog):
    product = by_name(catalog, "RX3 1P 16А")
    matches = SearchService(catalog).search(f"есть {product.article} в наличии?")
    assert matches[0].product.id == product.id
    assert matches[0].kind is MatchKind.EXACT


def test_supplier_article(catalog):
    product = by_name(catalog, "RX3 1P 16А")
    assert SearchService(catalog).search(product.supplier_article)[0].product.id == product.id


def test_alternative_for_zero_stock_keeps_key_parameters(catalog):
    source = by_name(catalog, "RX3 1P 16А")
    assert source.stock.status is StockStatus.OUT_OF_STOCK
    result = AlternativeService(catalog).find(source)
    assert result.alternatives
    for alt in result.alternatives:
        assert alt.product.stock.status is StockStatus.IN_STOCK
        assert alt.product.attribute("rated_current").value == "16А"
        assert alt.product.attribute("poles").value == "1"
        assert "номинальный ток" in alt.reason


def test_no_auto_alternative_for_conflicting_product(catalog):
    source = by_name(catalog, "DRX250")
    result = AlternativeService(catalog).find(source)
    assert result.alternatives == ()
    assert "Номинальный ток" in result.blocked_by


def test_no_alternatives_without_key_attributes(catalog):
    from dataclasses import replace
    source = replace(by_name(catalog, "Mosaic"), attributes=())
    result = AlternativeService(catalog).find(source)
    assert result.alternatives == ()
    assert result.not_matched_reason == "no_attributes"


def test_cable_cross_sections_are_different():
    from app.modules.catalog.quality import canonical
    assert canonical("3х2,5") != canonical("3х4")
    assert canonical("3х2,5") == canonical("3x2.5")
    assert canonical("4,5кА") == canonical("4.5 кА")


def test_barcode_lookup(catalog):
    product = by_name(catalog, "RX3 1P 16А")
    assert product.barcode
    assert SearchService(catalog).search(product.barcode)[0].product.id == product.id


def test_every_demo_item_without_stock_has_an_alternative(catalog):
    """Демо-каталог обязан показывать must have 2 в каждой категории, где есть товар без наличия."""
    missing = [p for p in catalog._by_id.values() if p.stock.status is not StockStatus.IN_STOCK]
    assert len(missing) >= 8
    for product in missing:
        result = AlternativeService(catalog).find(product)
        assert result.alternatives, (product.name, result.blocked_by)
        assert all(a.product.stock.status is StockStatus.IN_STOCK for a in result.alternatives)


def test_socket_alternative_keeps_current_voltage_and_mounting(catalog):
    source = by_name(catalog, "Mosaic")
    for alt in AlternativeService(catalog).find(source).alternatives:
        for key in ("rated_current", "voltage", "mounting"):
            assert alt.product.attribute(key).value == source.attribute(key).value
