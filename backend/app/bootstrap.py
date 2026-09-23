import asyncio
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.storage.sqlite import SQLiteStateStore
from app.core.config import Settings
from app.integrations.ports import ActionsPort, CatalogPort, DocumentsPort
from app.integrations.unavailable import UnavailableActions, UnavailableCatalog, UnavailableDocuments
from app.modules.assistant.runtime import GroundedAssistant
from app.modules.chat.ports import StateStore
from app.modules.chat.application import ChatService
from app.worker import Worker


@dataclass
class ContainerResources:
    """Resources acquired by bootstrap, closed once even when startup fails."""
    stack: ExitStack = field(default_factory=ExitStack)
    closed: bool = False

    async def aclose(self, planner=None):
        if self.closed:
            return
        self.closed = True
        try:
            if planner is not None:
                await planner.aclose()
        finally:
            # Pools and the synchronous EKT client must not block the ASGI loop.
            await asyncio.to_thread(self.stack.close)


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
    resources: ContainerResources = field(default_factory=ContainerResources, repr=False, compare=False)

    async def aclose(self):
        await self.resources.aclose(getattr(self.worker.processor, "planner", None))


def _catalog_db(settings: Settings):
    """Каталог из PostgreSQL. Корзина пока демо в SQLite рядом с сессиями."""
    from app.adapters.postgres.catalog import PostgresCatalog
    from app.infrastructure.db import create_pool
    from app.integrations.domain import DomainCatalogAdapter, SQLiteDemoActions
    from app.modules.catalog.sync import coverage

    with ExitStack() as cleanup:
        pool = create_pool(settings.database_url.get_secret_value())
        cleanup.callback(pool.close)
        live = None
        if settings.ekt_live_refresh:
            from app.adapters.ekt.client import EktClient
            live = EktClient(settings.ekt_api_user.get_secret_value(), settings.ekt_api_password.get_secret_value())
            cleanup.callback(live.close)
        reader = PostgresCatalog(pool, live)
        info = coverage(pool)
        # Реальным каталог считаем, только если в БД нет синтетических товаров.
        demo = "ekt" not in info["products"] or "synthetic" in info["products"]
        catalog = DomainCatalogAdapter(reader, "synthetic_catalog" if demo else "ekt_catalog", demo, info["coverage"])
        actions = SQLiteDemoActions(settings.local_db_path, reader)
        return catalog, actions, cleanup.pop_all(), demo


def build_container(settings: Settings, *, store: StateStore | None = None,
                    catalog: CatalogPort | None = None, documents: DocumentsPort | None = None,
                    actions: ActionsPort | None = None) -> Container:
    resources = ContainerResources()
    try:
        return _build_container(settings, store=store, catalog=catalog, documents=documents,
                                actions=actions, resources=resources)
    except BaseException:
        resources.stack.close()
        raise


def _build_container(settings, *, store, catalog, documents, actions, resources):
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
        from app.adapters.storage.catalog import SQLiteCatalog
        from app.integrations.domain import SQLiteDemoActions, SyntheticCatalogAdapter
        demo_catalog = SQLiteCatalog(settings.local_db_path)
        demo_catalog.seed_if_empty(
            Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ekt_products.json"
        )
        # Do not mix an injected live catalog with demo action validation.
        if catalog is not None or actions is not None:
            raise ValueError("Synthetic mode owns both catalog and actions; use unavailable mode for injection")
        catalog = SyntheticCatalogAdapter(demo_catalog)
        actions = SQLiteDemoActions(settings.local_db_path, demo_catalog)
    catalog_demo = synthetic
    if settings.integration_mode == "catalog_db":
        if catalog is not None or actions is not None:
            raise ValueError("catalog_db mode owns catalog and actions")
        catalog, actions, cleanup, catalog_demo = _catalog_db(settings)
        resources.stack.callback(cleanup.close)
    demo_cart = synthetic or settings.integration_mode == "catalog_db"
    capabilities = {
        "chat": "ready",
        "catalog": "synthetic_demo" if catalog_demo else "ready" if catalog is not None else "requires_integration",
        "documents": "ready" if documents is not None else "requires_integration",
        "cart": "synthetic_demo" if demo_cart else "ready" if actions is not None else "requires_integration",
        "llm": "openai" if settings.llm_enabled else "disabled",
        "upload": "ready" if settings.uploads_enabled else "requires_integration",
    }
    state = store if store is not None else SQLiteStateStore(settings.local_db_path)
    catalog_port = catalog if catalog is not None else UnavailableCatalog()
    documents_port = documents if documents is not None else UnavailableDocuments()
    actions_port = actions if actions is not None else UnavailableActions()
    processor = GroundedAssistant(catalog_port, actions_port, documents_port, settings.local_db_path)
    if settings.llm_enabled:
        from app.adapters.llm.openai_provider import OpenAIPlanningProvider
        processor.planner = OpenAIPlanningProvider(
            db_path=settings.local_db_path, api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model, timeout_s=settings.llm_timeout_seconds,
            max_output_tokens=settings.llm_max_output_tokens,
            session_budget=settings.llm_session_daily_tokens, site_budget=settings.llm_site_daily_tokens,
        )
    return Container(
        settings, state, catalog_port, documents_port, actions_port,
        ChatService(state, documents_port, settings),
        Worker(state, processor, documents_port, settings), capabilities, resources,
    )
