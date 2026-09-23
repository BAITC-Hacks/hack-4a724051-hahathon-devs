import pytest

from app.adapters.memory import InMemoryCartStore, InMemoryCatalog, InMemoryProposalStore
from app.modules.actions.service import ActionService


@pytest.fixture
def catalog():
    return InMemoryCatalog.from_file()


@pytest.fixture
def actions(catalog):
    return ActionService(catalog, InMemoryProposalStore(), InMemoryCartStore(), cart_url="/cart")


def by_name(catalog, fragment):
    return next(p for p in catalog._by_id.values() if fragment in p.name)
