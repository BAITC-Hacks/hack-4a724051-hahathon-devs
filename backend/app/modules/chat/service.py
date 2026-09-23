import asyncio
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.ports import DocumentsPort
from app.modules.chat.models import Session, TurnInput, TurnSubmission
from app.modules.chat.ports import StateStore


async def resolve_documents(port: DocumentsPort, session_id: str, asset_ids: list[str]):
    if not asset_ids:
        return []
    try:
        async with asyncio.timeout(5):
            documents = await port.resolve(session_id, asset_ids)
    except TimeoutError:
        raise AppError("documents_unavailable", "Проверка файлов временно недоступна.", 503, True)
    if [str(item.asset_id) for item in documents] != asset_ids:
        raise AppError("document_contract_error", "Не удалось проверить все вложения.", 503)
    return documents


class ChatService:
    def __init__(self, store: StateStore, documents: DocumentsPort, settings: Settings):
        self.store = store
        self.documents = documents
        self.settings = settings

    async def submit(self, session: Session, conversation_id: str, key: str,
                     payload: TurnInput) -> TurnSubmission:
        replay = await asyncio.to_thread(self.store.replay, session.id, conversation_id, key, payload)
        if replay is not None:
            return replay
        await resolve_documents(self.documents, session.id, [str(x) for x in payload.asset_ids])
        return await asyncio.to_thread(
            self.store.submit, session.id, conversation_id, key, payload, time.time(),
            self.settings.max_pending_jobs, self.settings.max_turns_per_session,
        )
