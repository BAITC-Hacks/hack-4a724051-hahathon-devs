"""Хранилища предложений и корзины. Таблицы и реализацию делает участник 2.

Требования к реализации, на которые опирается ActionService:
- все чтения ограничены session_id, чужой proposal или корзина не находятся;
- compare_and_set_status атомарен (UPDATE ... WHERE id=? AND status=?);
- apply_to_cart атомарен и идемпотентен по operation_id: повтор с тем же
  operation_id возвращает уже применённую корзину и ничего не добавляет.
"""

from typing import Protocol

from app.modules.actions.models import Cart, Proposal, ProposalStatus, Receipt


class CartVersionConflict(Exception):
    """Корзину изменили между чтением и записью."""


class ProposalStore(Protocol):
    def save(self, proposal: Proposal) -> None: ...

    def get(self, session_id: str, proposal_id: str) -> Proposal | None: ...

    def open_for_session(self, session_id: str) -> list[Proposal]:
        """Предложения в статусе PROPOSED, новые первыми."""

    def compare_and_set_status(
        self, proposal_id: str, expected: ProposalStatus, new: ProposalStatus,
        reason: str | None = None, receipt: Receipt | None = None,
    ) -> bool: ...


class CartStore(Protocol):
    def get_cart(self, session_id: str) -> Cart: ...

    def apply_to_cart(
        self, session_id: str, additions: dict[int, int], expected_version: int, operation_id: str,
    ) -> Cart:
        """Прибавить количества к строкам корзины. Бросает CartVersionConflict."""
