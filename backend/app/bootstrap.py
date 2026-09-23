from dataclasses import dataclass
from pathlib import Path

from app.adapters.storage.sqlite import SQLiteStateStore
from app.core.config import Settings
from app.integrations.ports import ActionsPort, CatalogPort, DocumentsPort
from app.integrations.unavailable import UnavailableActions, UnavailableCatalog, UnavailableDocuments
from app.modules.assistant.runtime import GroundedAssistant
from app.modules.chat.ports import StateStore
from app.modules.chat.application import ChatService
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
    synthetic = settings.integration_mode == "synthetic"
    if settings.uploads_enabled and documents is None:
        from app.modules.documents.service import DocumentsService
        documents = DocumentsService(settings.local_db_path, settings.assets_root, settings.scanner_command)
        documents.initialize()
    if synthetic:
        from app.adapters.memory import InMemoryCatalog
        from app.integrations.domain import SQLiteDemoActions, SyntheticCatalogAdapter
        demo_catalog = InMemoryCatalog.from_file(
            Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ekt_products.json"
        )
        # Do not mix an injected live catalog with demo action validation.
        if catalog is not None or actions is not None:
            raise ValueError("Synthetic mode owns both catalog and actions; use unavailable mode for injection")
        catalog = SyntheticCatalogAdapter(demo_catalog)
        actions = SQLiteDemoActions(settings.local_db_path, demo_catalog)
    capabilities = {
        "chat": "ready",
        "catalog": "synthetic_demo" if synthetic else "ready" if catalog is not None else "requires_integration",
        "documents": "ready" if documents is not None else "requires_integration",
        "cart": "synthetic_demo" if synthetic else "ready" if actions is not None else "requires_integration",
        "llm": "openai" if settings.llm_enabled else "disabled",
        "upload": "ready" if settings.uploads_enabled else "requires_integration",
    }
    state = store if store is not None else SQLiteStateStore(settings.local_db_path)
    catalog_port = catalog if catalog is not None else UnavailableCatalog()
    documents_port = documents if documents is not None else UnavailableDocuments()
    actions_port = actions if actions is not None else UnavailableActions()
    planner = None
    if settings.llm_enabled:
        from app.adapters.llm.openai_provider import OpenAIPlanningProvider
        planner = OpenAIPlanningProvider(
            db_path=settings.local_db_path, api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model, timeout_s=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
            session_budget=settings.llm_session_daily_tokens, site_budget=settings.llm_site_daily_tokens,
        )
    return Container(
        settings, state, catalog_port, documents_port, actions_port,
        ChatService(state, documents_port, settings),
        Worker(state, GroundedAssistant(catalog_port, actions_port, documents_port, settings.local_db_path, planner),
               documents_port, settings),
        capabilities,
    )
