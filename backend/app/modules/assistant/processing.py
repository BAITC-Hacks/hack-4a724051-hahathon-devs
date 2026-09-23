from dataclasses import dataclass
from typing import Protocol

from app.contracts import ParsedDocument
from app.modules.chat.models import AssistantOutput, MessageView, TurnInput


@dataclass(frozen=True)
class ProcessingContext:
    session_id: str
    payload: TurnInput
    history: tuple[MessageView, ...]
    documents: tuple[ParsedDocument, ...]
    turn_id: str = ""
    conversation_id: str = ""


class AssistantProcessor(Protocol):
    async def process(self, context: ProcessingContext) -> AssistantOutput: ...
