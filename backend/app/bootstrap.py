from dataclasses import dataclass
from pathlib import Path

from app.adapters.storage.sqlite import SQLiteStateStore
from app.core.config import Settings
from app.integrations.ports import ActionsPort, CatalogPort, DocumentsPort
from app.integrations.unavailable import UnavailableActions, UnavailableCatalog, UnavailableDocuments
from app.modules.assistant.catalog_only import CatalogOnlyProcessor
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


def _catalog_db(settings: Settings):
    """Каталог из PostgreSQL. Корзина пока демо в SQLite рядом с сессиями."""
    from app.adapters.postgres.catalog import PostgresCatalog
    from app.infrastructure.db import create_pool
    from app.integrations.domain import DomainCatalogAdapter, SQLiteDemoActions
    from app.modules.catalog.sync import coverage

    pool = create_pool(settings.database_url.get_secret_value())
    live = None
    if settings.ekt_live_refresh:
        from app.adapters.ekt.client import EktClient
        live = EktClient(settings.ekt_api_user.get_secret_value(), settings.ekt_api_password.get_secret_value())
    reader = PostgresCatalog(pool, live)
    documents = _documents(settings, pool)
    info = coverage(pool)
    # Реальным каталог считаем, только если в БД нет синтетических товаров.
    demo = "ekt" not in info["products"] or "synthetic" in info["products"]
    catalog = DomainCatalogAdapter(reader, "synthetic_catalog" if demo else "ekt_catalog", demo, info["coverage"])
    return catalog, SQLiteDemoActions(settings.local_db_path, reader), documents, demo


def _documents(settings: Settings, pool):
    from app.adapters.postgres.documents import LocalBlobStore, PostgresDocuments
    from app.modules.documents.scanner import ClamdScanner, NoScanner

    scanner = ClamdScanner(settings.clamd_socket) if settings.clamd_socket else NoScanner()
    return PostgresDocuments(pool, LocalBlobStore(settings.assets_dir), scanner,
                             allow_unscanned=settings.documents_allow_unscanned)


def build_container(settings: Settings, *, store: StateStore | None = None,
                    catalog: CatalogPort | None = None, documents: DocumentsPort | None = None,
                    actions: ActionsPort | None = None) -> Container:
    if settings.app_env == "production":
        raise ValueError(
            "This development slice is not approved for production. "
            "Integrate PostgreSQL, deployment security and operational checks first."
        )
    synthetic = settings.integration_mode == "synthetic"
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
    catalog_demo = synthetic
    if settings.integration_mode == "catalog_db":
        if catalog is not None or actions is not None or documents is not None:
            raise ValueError("catalog_db mode owns catalog, actions and documents")
        catalog, actions, documents, catalog_demo = _catalog_db(settings)
    demo_cart = synthetic or settings.integration_mode == "catalog_db"
    capabilities = {
        "chat": "ready",
        "catalog": "synthetic_demo" if catalog_demo else "ready" if catalog is not None else "requires_integration",
        "documents": "ready" if documents is not None else "requires_integration",
        "cart": "synthetic_demo" if demo_cart else "ready" if actions is not None else "requires_integration",
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
