"""Сборка доменного чата из портов каталога, корзины и истории."""

from app.modules.actions.ports import CartStore, ProposalStore
from app.modules.actions.service import ActionService
from app.modules.assistant.config import AssistantConfig
from app.modules.assistant.runner import AssistantRunner
from app.modules.assistant.tools import ToolBox
from app.modules.catalog.ports import CatalogReader
from app.modules.chat.service import ChatService, ConversationStore
from app.modules.knowledge.service import PurchaseTermsService
from app.modules.search.alternatives import AlternativeService
from app.modules.search.service import SearchService


def build_chat_service(catalog: CatalogReader, proposals: ProposalStore, carts: CartStore,
                       conversations: ConversationStore, cart_url: str = "/cart",
                       config: AssistantConfig | None = None, provider=None) -> ChatService:
    config = config or AssistantConfig.from_env()
    actions = ActionService(catalog, proposals, carts, cart_url)
    toolbox = ToolBox(catalog, SearchService(catalog), AlternativeService(catalog), PurchaseTermsService(), actions)
    if provider is None and config.llm_enabled:
        from app.adapters.llm.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(config.api_key, config.model, config.effort, config.timeout_s)
    runner = AssistantRunner(provider, toolbox, config, cart_url) if provider is not None else None
    return ChatService(actions, toolbox, conversations, runner, cart_url, config.history_messages)
