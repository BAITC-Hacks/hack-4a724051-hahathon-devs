import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AssistantConfig:
    llm_enabled: bool
    api_key: str | None
    model: str
    effort: str
    max_steps: int  # сколько раз за ход можно обратиться к модели
    max_tool_calls: int
    timeout_s: float
    history_messages: int

    @classmethod
    def from_env(cls) -> "AssistantConfig":
        return cls(
            llm_enabled=_flag("LLM_ENABLED", False),
            api_key=os.getenv("LLM_API_KEY") or None,
            model=os.getenv("LLM_MODEL", "claude-opus-5"),
            effort=os.getenv("LLM_EFFORT", "low"),
            max_steps=int(os.getenv("LLM_MAX_STEPS", "5")),
            max_tool_calls=int(os.getenv("LLM_MAX_TOOL_CALLS", "12")),
            timeout_s=float(os.getenv("LLM_TIMEOUT_S", "25")),
            history_messages=int(os.getenv("LLM_HISTORY_MESSAGES", "12")),
        )
