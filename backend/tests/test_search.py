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
