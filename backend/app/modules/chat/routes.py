import time
from uuid import UUID

from fastapi import APIRouter, Request

from app.api.v1.dependencies import IdempotencyKey, Services, SessionRead, SessionWrite
from app.api.v1.responses import success
from app.contracts import Envelope
from app.modules.chat.models import (
    ConversationView, MessageView, TurnInput, TurnSubmission, TurnView,
)

router = APIRouter(tags=["chat"])


@router.post("/conversations", status_code=201, response_model=Envelope[ConversationView])
def create_conversation(request: Request, services: Services, session: SessionWrite):
    return success(request, services.store.create_conversation(
        session.id, time.time(), services.settings.max_conversations,
    ))


@router.get("/conversations/{conversation_id}/messages", response_model=Envelope[list[MessageView]])
def get_messages(conversation_id: UUID, request: Request, services: Services, session: SessionRead):
    return success(request, services.store.messages(session.id, str(conversation_id), 50))


@router.post("/conversations/{conversation_id}/turns", status_code=202,
             response_model=Envelope[TurnSubmission])
async def submit_turn(conversation_id: UUID, payload: TurnInput, request: Request,
                      services: Services, session: SessionWrite, key: IdempotencyKey):
    return success(request, await services.chat.submit(session, str(conversation_id), key, payload))


@router.get("/conversations/{conversation_id}/turns", response_model=Envelope[list[TurnView]])
def conversation_turns(conversation_id: UUID, request: Request, services: Services, session: SessionRead):
    return success(request, services.store.turns(session.id, str(conversation_id), 50))


@router.get("/turns/{turn_id}", response_model=Envelope[TurnView])
def get_turn(turn_id: UUID, request: Request, services: Services, session: SessionRead):
    return success(request, services.store.get_turn(session.id, str(turn_id)))


@router.post("/turns/{turn_id}/cancel", response_model=Envelope[TurnView])
def cancel_turn(turn_id: UUID, request: Request, services: Services, session: SessionWrite):
    return success(request, services.store.cancel(session.id, str(turn_id), time.time()))
