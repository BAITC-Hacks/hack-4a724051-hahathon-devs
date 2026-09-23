"""Claude через официальный SDK anthropic. Единственное место, где импортируется SDK."""

import anthropic

from app.modules.assistant.ports import LlmTurn, LlmUnavailable, Message, ToolCall, ToolSpec


def _user_content(message: Message) -> list[dict]:
    content: list[dict] = [
        {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
        for r in message.tool_results
    ]
    for part in message.parts:
        if part.type == "text":
            content.append({"type": "text", "text": part.text})
        elif part.type in ("image", "document"):
            content.append({
                "type": part.type,
                "source": {"type": "base64", "media_type": part.media_type, "data": part.data},
            })
    return content


def _assistant_content(message: Message):
    if message.raw is not None:
        return message.raw  # блоки ответа как пришли, вместе с thinking
    content: list[dict] = [{"type": "text", "text": p.text} for p in message.parts if p.type == "text" and p.text]
    content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in message.tool_calls]
    return content


class AnthropicProvider:
    def __init__(self, api_key: str | None, model: str, effort: str = "low", timeout_s: float = 25.0):
        # Без автоповторов: повтор платного запроса решает runner, а не SDK.
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)
        self.model = model
        self.effort = effort

    def generate(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LlmTurn:
        api_messages = [
            {"role": m.role, "content": _assistant_content(m) if m.role == "assistant" else _user_content(m)}
            for m in messages
        ]
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=api_messages,
                tools=[{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                       for t in tools],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError) as e:
            raise LlmUnavailable(type(e).__name__) from e

        calls, texts = [], []
        for block in response.content:
            if block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, dict(block.input or {})))
            elif block.type == "text":
                texts.append(block.text)

        stop = {"end_turn": "end", "tool_use": "tool_use", "max_tokens": "max_tokens",
                "refusal": "refusal"}.get(response.stop_reason, "other")
        return LlmTurn(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            stop=stop,
            message=Message(role="assistant", tool_calls=calls, raw=response.content),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
