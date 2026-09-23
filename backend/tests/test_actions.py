import threading
from dataclasses import replace
from decimal import Decimal

from app.modules.actions.models import ProposalStatus
from app.modules.actions.service import is_explicit_confirmation, is_explicit_rejection
from tests.conftest import by_name


def in_stock(catalog):
    return by_name(catalog, "ВА47-29 1P 10А")


def test_proposal_does_not_touch_cart(catalog, actions):
    result = actions.prepare_proposal("s1", [(in_stock(catalog).id, 2)])
    assert result.ok
    assert actions.carts.get_cart("s1").lines == {}


def test_confirm_adds_once(catalog, actions):
    product = in_stock(catalog)
    proposal = actions.prepare_proposal("s1", [(product.id, 2)]).proposal
    first = actions.confirm_and_apply("s1", proposal.id)
    second = actions.confirm_and_apply("s1", proposal.id)
    assert first.ok and second.ok
    assert actions.carts.get_cart("s1").lines == {product.id: 2}
    assert first.proposal.receipt.cart_url == "/cart"


def test_parallel_confirms_do_not_duplicate(catalog, actions):
    product = in_stock(catalog)
    proposal = actions.prepare_proposal("s1", [(product.id, 1)]).proposal
    threads = [threading.Thread(target=actions.confirm_and_apply, args=("s1", proposal.id)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert actions.carts.get_cart("s1").lines == {product.id: 1}


def test_quantity_limited_by_stock(catalog, actions):
    product = in_stock(catalog)
    too_many = product.stock.sellable_quantity + 1
    result = actions.prepare_proposal("s1", [(product.id, too_many)])
    assert not result.ok
    assert result.problems[0].code == "exceeds_stock"


def test_stock_counts_what_is_already_in_cart(catalog, actions):
    product = in_stock(catalog)
    stock = product.stock.sellable_quantity
    p1 = actions.prepare_proposal("s1", [(product.id, stock)]).proposal
    actions.confirm_and_apply("s1", p1.id)
    assert actions.prepare_proposal("s1", [(product.id, 1)]).problems[0].code == "exceeds_stock"


def test_out_of_stock_rejected(catalog, actions):
    result = actions.prepare_proposal("s1", [(by_name(catalog, "RX3 1P 16А").id, 1)])
    assert result.problems[0].code == "out_of_stock"


def test_fractional_or_negative_quantity(catalog, actions):
    pid = in_stock(catalog).id
    assert actions.prepare_proposal("s1", [(pid, 1.5)]).problems[0].code == "bad_quantity"
    assert actions.prepare_proposal("s1", [(pid, -1)]).problems[0].code == "bad_quantity"


def test_other_session_cannot_confirm(catalog, actions):
    proposal = actions.prepare_proposal("s1", [(in_stock(catalog).id, 1)]).proposal
    result = actions.confirm_and_apply("intruder", proposal.id)
    assert not result.ok
    assert actions.carts.get_cart("s1").lines == {}
    assert actions.carts.get_cart("intruder").lines == {}


def test_price_change_makes_proposal_stale(catalog, actions):
    product = in_stock(catalog)
    proposal = actions.prepare_proposal("s1", [(product.id, 1)]).proposal
    catalog.replace_product(replace(product, price=product.price + Decimal(100)))
    result = actions.confirm_and_apply("s1", proposal.id)
    assert not result.ok
    assert result.proposal.status is ProposalStatus.STALE
    assert actions.carts.get_cart("s1").lines == {}


def test_new_proposal_supersedes_old(catalog, actions):
    pid = in_stock(catalog).id
    old = actions.prepare_proposal("s1", [(pid, 1)]).proposal
    new = actions.prepare_proposal("s1", [(pid, 3)]).proposal
    assert actions.current_proposal("s1").id == new.id
    assert not actions.confirm_and_apply("s1", old.id).ok


def test_rejected_proposal_cannot_be_applied(catalog, actions):
    proposal = actions.prepare_proposal("s1", [(in_stock(catalog).id, 1)]).proposal
    assert actions.reject("s1", proposal.id).ok
    assert not actions.confirm_and_apply("s1", proposal.id).ok
    assert actions.carts.get_cart("s1").lines == {}


def test_confirmation_phrases():
    for text in ["Да", "да, добавь", "Добавь в корзину!", "подтверждаю", "Иә, қос"]:
        assert is_explicit_confirmation(text), text
    for text in ["расскажи про этот автомат", "а есть дешевле?", "да, но сначала покажи аналоги",
                 "может быть", "добавь 5 штук другого"]:
        assert not is_explicit_confirmation(text), text
    assert is_explicit_rejection("нет, не надо")
    assert is_explicit_rejection("не надо")
