from dataclasses import dataclass

from app.adapters.storage.sqlite import SQLiteStateStore
from app.core.config import Settings
from app.integrations.ports import ActionsPort, CatalogPort, DocumentsPort
from app.integrations.unavailable import UnavailableActions, UnavailableCatalog, UnavailableDocuments
from app.modules.assistant.runner import CatalogOnlyProcessor
from app.modules.chat.ports import StateStore
from app.modules.chat.service import ChatService
from app.worker import Worker


@dataclass(frozen=True)
class Container:
    settings: Settings
    store: StateStore
    catalog: CatalogPort
    documents: DocumentsPort
    actions: ActionsPort
    chat: ChatService
    worker: Worker
    capabilities: dict[str, str]


def build_container(settings: Settings, *, store: StateStore | None = None,
                    catalog: CatalogPort | None = None, documents: DocumentsPort | None = None,
                    actions: ActionsPort | None = None) -> Container:
    if settings.app_env == "production":
        raise ValueError(
            "This development slice is not approved for production. "
            "Integrate PostgreSQL, deployment security and operational checks first."
        )
    capabilities = {
        "chat": "ready",
        "catalog": "ready" if catalog is not None else "requires_integration",
        "documents": "ready" if documents is not None else "requires_integration",
        "cart": "ready" if actions is not None else "requires_integration",
        "llm": "disabled",
        "upload": "requires_integration",
    }
    state = store if store is not None else SQLiteStateStore(settings.local_db_path)
    catalog_port = catalog if catalog is not None else UnavailableCatalog()
    documents_port = documents if documents is not None else UnavailableDocuments()
    actions_port = actions if actions is not None else UnavailableActions()
    return Container(
        settings, state, catalog_port, documents_port, actions_port,
        ChatService(state, documents_port, settings),
        Worker(state, CatalogOnlyProcessor(catalog_port), documents_port, settings),
        capabilities,
    )
