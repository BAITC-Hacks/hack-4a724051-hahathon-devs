"""Нейтральный интерфейс LLM. SDK конкретного вендора живёт только в adapters/llm."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str  # JSON-строка
    is_error: bool = False


@dataclass(frozen=True)
class Part:
    """Кусок пользовательского сообщения: текст, картинка или PDF (base64)."""
    type: Literal["text", "image", "document"]
    text: str | None = None
    media_type: str | None = None
    data: str | None = None


@dataclass
class Message:
    role: Literal["user", "assistant"]
    parts: list[Part] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Ответ провайдера как есть, чтобы вернуть его без потерь в следующем шаге цикла.
    raw: Any = None


@dataclass(frozen=True)
class LlmTurn:
    text: str
    tool_calls: list[ToolCall]
    stop: Literal["end", "tool_use", "max_tokens", "refusal", "other"]
    message: Message
    input_tokens: int = 0
    output_tokens: int = 0


class LlmUnavailable(Exception):
    """Провайдер не ответил или отказал. Чат переходит на ответ без модели."""


class LlmProvider(Protocol):
    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn: ...
