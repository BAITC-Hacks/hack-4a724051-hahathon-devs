from dataclasses import replace

import pytest

from app.adapters.memory import InMemoryCatalog
from app.modules.catalog.models import Attribute, AttributeStatus
from app.modules.catalog.quality import canonical, product_from_source
from app.modules.search.alternatives import AlternativeService
from tests.conftest import by_name
from tests.test_catalog_quality import raw


def with_attribute(product, key, value=None, status=AttributeStatus.OK):
    original = product.attribute(key)
    attr = replace(original, value=value, status=status) if original else Attribute(key, key, value, None, status)
    return replace(product, attributes=tuple(a for a in product.attributes if a.key != key) + (attr,))


@pytest.mark.parametrize("left,right", [("3x2.5", "3x4"), ("230/400В", "230В"),
                                        ("ВА47-29", "ВА47-60"), ("AB001", "AB1"),
                                        ("IP40", "IP44"), ("30мА", "30А")])
def test_compound_values_and_unit_scale_are_not_collapsed(left, right):
    assert canonical(left) != canonical(right)


def test_equivalent_units_and_cable_notation():
    assert canonical("3х2,50") == canonical("3x2.5") == canonical("3 × 2.5")
    assert canonical("4500 A", "кА") == canonical("4,5кА", "кА")
    assert canonical("0.03А", "мА") == canonical("30мА", "мА")


@pytest.mark.parametrize("price", ["NaN", "sNaN", "Infinity", "-Infinity", float("nan"), float("inf"), "bad"])
def test_non_finite_or_malformed_prices_are_unknown(price):
    product = product_from_source(raw(price=price))
    assert product.price is None
    assert "price_missing" in product.warnings


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hello", "//evil.example/path",
                                 "/%2fexample.com/path", "/\\evil.example", "https://user:pass@example.com/a",
                                 "https://example.com/%0afoo", "https://[broken"])
def test_unsafe_product_image_and_certificate_links_are_removed(url):
    product = product_from_source(raw(url=url, image=url, certificates=[{"url": url}]))
    assert product.url == ""
    assert product.image is None
    assert product.certificates == ()
    assert not product.category_path


def test_decimal_current_name_does_not_create_false_conflict():
    product = product_from_source(raw(name="АВ 1P 16,5А", description="",
                                      properties={"NOMINALNYY_TOK": "16.5А"}))
    assert product.attribute("rated_current").status is AttributeStatus.OK


def test_conflicting_property_aliases_are_not_silently_ignored():
    product = product_from_source(raw(name="АВ", description="",
                                      properties={"NOMINALNYY_TOK": "16А", "NOMINALNYY_TOK_A_1": "25А"}))
    assert product.attribute("rated_current").status is AttributeStatus.CONFLICT


@pytest.mark.parametrize("key", ["rated_current", "poles", "voltage", "curve", "breaking_capacity"])
def test_missing_source_required_field_blocks_analogs(catalog, key):
    source = by_name(catalog, "RX3 1P 16А")
    source = replace(source, attributes=tuple(a for a in source.attributes if a.key != key))
    result = AlternativeService(catalog).find(source)
    assert result.alternatives == ()
    assert f"missing:{key}" in result.blocked_by


def test_capacity_never_downgrades_and_unknown_candidate_is_excluded(catalog):
    source = by_name(catalog, "RX3 1P 16А")
    base = replace(source, id=1, stock=catalog.get_product(900001).stock)
    lower = with_attribute(base, "breaking_capacity", "4.5кА")
    equal = replace(with_attribute(base, "breaking_capacity", "6000А"), id=2)
    higher = replace(with_attribute(base, "breaking_capacity", "10кА"), id=3)
    missing = replace(base, id=4, attributes=tuple(a for a in base.attributes if a.key != "breaking_capacity"))
    conflict = replace(with_attribute(base, "breaking_capacity", status=AttributeStatus.CONFLICT), id=5)
    result = AlternativeService(InMemoryCatalog([source, lower, equal, higher, missing, conflict])).find(source, 20)
    assert {a.product.id for a in result.alternatives} == {2, 3}
    assert all("совместимость требует проверки" in a.reason for a in result.alternatives)


def test_capacity_conflict_in_source_blocks_analogs(catalog):
    source = with_attribute(by_name(catalog, "RX3 1P 16А"), "breaking_capacity", status=AttributeStatus.CONFLICT)
    result = AlternativeService(catalog).find(source)
    assert not result.alternatives
    assert "Отключающая способность" in result.blocked_by


def test_unknown_category_has_no_unsafe_generic_fallback(catalog):
    source = replace(catalog.get_product(900001), category_path=("unknown",))
    candidate = replace(source, id=2)
    result = AlternativeService(InMemoryCatalog([source, candidate])).find(source)
    assert not result.alternatives
    assert "category_profile_unknown" in result.blocked_by


def test_cable_requires_construction_and_exact_complete_cross_section(catalog):
    source = catalog.get_product(900059)
    assert not AlternativeService(catalog).find(source).alternatives
    for key, value in [("cable_type", "ВВГнг-LS"), ("conductor_material", "медь"),
                       ("insulation", "ПВХ"), ("fire_class", "П1б.8.2.2.2")]:
        source = with_attribute(source, key, value)
    source = with_attribute(source, "cross_section", "3x2.5")
    matching = replace(source, id=1)
    thicker = replace(with_attribute(source, "cross_section", "3x4"), id=2)
    missing = replace(source, id=3, attributes=tuple(a for a in source.attributes if a.key != "insulation"))
    result = AlternativeService(InMemoryCatalog([source, matching, thicker, missing])).find(source)
    assert [a.product.id for a in result.alternatives] == [1]
