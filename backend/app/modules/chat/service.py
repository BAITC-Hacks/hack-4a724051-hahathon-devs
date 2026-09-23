"""Один ход чата: подтверждение корзины, ответ модели или ответ без модели.

Доменный ход чата. HTTP-слой и дальнейшая работа над ИИ у участника 2.
"""

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from app.modules.actions.models import ProposalStatus
from app.modules.actions.service import ActionService, is_explicit_confirmation, is_explicit_rejection
from app.modules.assistant.contracts import ChatReply, ProductCard, ProposalView
from app.modules.assistant.ports import LlmUnavailable, Message, Part
from app.modules.assistant.rendering import applied_text
from app.modules.assistant.runner import AssistantRunner, facts_text
from app.modules.assistant.tools import Facts, ToolBox
from app.modules.catalog.models import StockStatus

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 8000
MAX_ATTACHMENT_CHARS = 20000


@dataclass(frozen=True)
class Attachment:
    """Файл клиента после проверки и разбора (разбор делает участник 1).

    kind="text": извлечённый текст (xlsx, docx, pdf с текстом).
    kind="image" / "pdf": исходник в base64, модель прочитает его сама.
    """
    name: str
    kind: Literal["text", "image", "pdf"]
    text: str | None = None
    data_base64: str | None = None
    media_type: str | None = None
    note: str | None = None  # например "обработано 200 из 350 строк"


class ConversationStore(Protocol):
    def history(self, session_id: str, limit: int = 20) -> list[dict]:
        """[{"role": "user"|"assistant", "content": str}], старые первыми."""

    def append(self, session_id: str, role: str, content: str) -> None: ...


class ChatService:
    def __init__(self, actions: ActionService, toolbox: ToolBox, conversations: ConversationStore,
                 runner: AssistantRunner | None, cart_url: str, history_messages: int = 12):
        self.actions = actions
        self.toolbox = toolbox
        self.conversations = conversations
        self.runner = runner
        self.cart_url = cart_url
        self.history_messages = history_messages

    # --- действия с корзиной, без модели ---

    def confirm(self, session_id: str, proposal_id: str, payload_hash: str | None = None) -> ChatReply:
        result = self.actions.confirm_and_apply(session_id, proposal_id, payload_hash)
        if result.ok and result.proposal and result.proposal.status is ProposalStatus.APPLIED:
            text = applied_text(result.proposal, result.proposal.receipt.cart_url)
        else:
            text = result.message
            if result.problems:
                text += "\n" + "\n".join(p.message for p in result.problems)
        reply = ChatReply(text, proposal=ProposalView.from_proposal(result.proposal) if result.proposal else None,
                          mode="action")
        self.conversations.append(session_id, "assistant", reply.message)
        return reply

    def reject(self, session_id: str, proposal_id: str) -> ChatReply:
        result = self.actions.reject(session_id, proposal_id)
        reply = ChatReply(result.message, proposal=ProposalView.from_proposal(result.proposal)
                          if result.proposal else None, mode="action")
        self.conversations.append(session_id, "assistant", reply.message)
        return reply

    # --- обычное сообщение ---

    def handle_message(self, session_id: str, text: str, attachments: list[Attachment] | None = None,
                       page_product_id: int | None = None) -> ChatReply:
        text = (text or "").strip()[:MAX_MESSAGE_CHARS]
        attachments = attachments or []

        current = self.actions.current_proposal(session_id)
        if current and not attachments:
            if is_explicit_confirmation(text):
                self.conversations.append(session_id, "user", text)
                return self.confirm(session_id, current.id, current.payload_hash)
            if is_explicit_rejection(text):
                self.conversations.append(session_id, "user", text)
                return self.reject(session_id, current.id)

        history = self.conversations.history(session_id, self.history_messages)
        user_parts = self._user_parts(text, attachments, page_product_id, current)
        log_text = text + "".join(f"\n[файл: {a.name}]" for a in attachments)

        reply = None
        if self.runner is not None:
            try:
                result = self.runner.run(session_id, self._history_messages(history), user_parts)
                reply = self._reply(result.text, result.facts, result.mode, result.warnings)
            except LlmUnavailable as e:
                log.warning("llm unavailable: %s", e)
        if reply is None:
            reply = self._answer_without_llm(session_id, text, attachments)

        self.conversations.append(session_id, "user", log_text)
        self.conversations.append(session_id, "assistant", reply.message)
        return reply

    def _reply(self, text: str, facts: Facts, mode: str, warnings: list[str]) -> ChatReply:
        return ChatReply(
            message=text,
            products=[ProductCard.from_product(facts.products[pid]) for pid in facts.shown[:8]],
            proposal=ProposalView.from_proposal(facts.proposal) if facts.proposal else None,
            sources=list(facts.sources),
            mode=mode,
            warnings=warnings,
        )

    @staticmethod
    def _history_messages(history: list[dict]) -> list[Message]:
        messages = [Message(role=h["role"], parts=[Part("text", text=h["content"])])
                    for h in history if h.get("content")]
        while messages and messages[0].role != "user":  # история должна начинаться с клиента
            messages.pop(0)
        merged: list[Message] = []
        for m in messages:  # роли должны чередоваться
            if merged and merged[-1].role == m.role:
                merged[-1].parts.extend(m.parts)
            else:
                merged.append(m)
        return merged if not merged or merged[-1].role == "assistant" else merged[:-1]

    def _user_parts(self, text, attachments, page_product_id, current) -> list[Part]:
        parts: list[Part] = []
        context = []
        if page_product_id:
            context.append(f"Клиент сейчас на странице товара id={int(page_product_id)}.")
        if current:
            context.append("Есть неподтверждённое предложение в корзину: "
                           + "; ".join(f"{i.name} × {i.quantity}" for i in current.items)
                           + ". Если клиент меняет состав, подготовь новое через propose_cart.")
        if context:
            parts.append(Part("text", text="[Контекст сайта] " + " ".join(context)))
        for a in attachments:
            header = f'<attachment name="{a.name}"' + (f' note="{a.note}"' if a.note else "") + ">"
            if a.kind == "text" and a.text:
                parts.append(Part("text", text=f"{header}\n{a.text[:MAX_ATTACHMENT_CHARS]}\n</attachment>"))
            elif a.kind == "image" and a.data_base64:
                parts.append(Part("text", text=f"{header} изображение ниже </attachment>"))
                parts.append(Part("image", media_type=a.media_type or "image/jpeg", data=a.data_base64))
            elif a.kind == "pdf" and a.data_base64:
                parts.append(Part("text", text=f"{header} документ ниже </attachment>"))
                parts.append(Part("document", media_type="application/pdf", data=a.data_base64))
        parts.append(Part("text", text=text or "(клиент прислал только файлы)"))
        return parts

    def _answer_without_llm(self, session_id: str, text: str, attachments: list[Attachment]) -> ChatReply:
        """Модель выключена или недоступна: отвечаем поиском и шаблоном, без выдумок."""
        facts = Facts()
        topics = self.toolbox.terms.match(text)
        if topics:
            self.toolbox._terms({"topics": [t.id for t in topics]}, session_id, facts)
        matches = self.toolbox.search.search(text, limit=3) if not topics else []
        for m in matches:
            facts.remember(m.product)
            if m.product.stock.status in (StockStatus.OUT_OF_STOCK, StockStatus.NOT_SELLABLE):
                for alt in self.toolbox.alternatives.find(m.product).alternatives:
                    facts.remember(alt.product)
        intro = ""
        if attachments:
            intro = "Файлы сейчас разобрать не получилось, пришлите артикулы текстом."
        elif matches and matches[0].product.stock.status is not StockStatus.IN_STOCK:
            intro = "Этого товара сейчас нет, ниже похожие позиции в наличии:"
        return self._reply(facts_text(facts, intro), facts, "fallback", ["llm_unavailable"])
