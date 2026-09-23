"""Ограниченный цикл «модель → инструмент → модель» на один ход диалога."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.assistant.config import AssistantConfig
from app.modules.assistant.policy import find_violations
from app.modules.assistant.ports import LlmProvider, LlmUnavailable, Message, Part, ToolResult
from app.modules.assistant.rendering import product_line, proposal_text
from app.modules.assistant.tools import Facts, ToolBox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "consultant.md").read_text(encoding="utf-8")


@dataclass
class RunResult:
    text: str
    facts: Facts
    mode: str  # llm | verified_fallback
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def facts_text(facts: Facts, intro: str = "") -> str:
    lines = [intro] if intro else []
    lines += [product_line(facts.products[pid]) for pid in facts.shown[:6]]
    if facts.terms_text:
        lines += facts.terms_text
    if facts.proposal:
        lines.append(proposal_text(facts.proposal))
    if len(lines) <= (1 if intro else 0):
        lines.append("Не смог надёжно ответить на этот вопрос. Уточните артикул или название товара, "
                     "либо свяжитесь с менеджером через раздел «Контакты» на сайте.")
    return "\n".join(lines)


class AssistantRunner:
    def __init__(self, provider: LlmProvider, toolbox: ToolBox, config: AssistantConfig, cart_url: str):
        self.provider = provider
        self.toolbox = toolbox
        self.config = config
        self.cart_url = cart_url

    def run(self, session_id: str, history: list[Message], user_parts: list[Part]) -> RunResult:
        facts = Facts()
        messages = [*history, Message(role="user", parts=user_parts)]
        tools = self.toolbox.specs()
        tool_calls_used, proposed = 0, False
        usage_in = usage_out = 0
        final_text = ""

        for _ in range(self.config.max_steps):
            turn = self.provider.generate(SYSTEM_PROMPT, messages, tools)
            usage_in += turn.input_tokens
            usage_out += turn.output_tokens
            if turn.stop == "refusal":
                raise LlmUnavailable("refusal")
            messages.append(turn.message)
            if not turn.tool_calls:
                final_text = turn.text
                break
            results = []
            for call in turn.tool_calls:
                tool_calls_used += 1
                if tool_calls_used > self.config.max_tool_calls:
                    results.append(ToolResult(call.id, '{"error": "tool_limit_reached"}', is_error=True))
                elif call.name == "propose_cart" and proposed:
                    results.append(ToolResult(call.id, '{"error": "one_proposal_per_turn"}', is_error=True))
                else:
                    result = self.toolbox.execute(call, session_id, facts)
                    proposed = proposed or (call.name == "propose_cart" and facts.proposal is not None)
                    results.append(result)
            messages.append(Message(role="user", tool_results=results))

        if not final_text:
            return RunResult(facts_text(facts), facts, "verified_fallback", ["step_limit"], usage_in, usage_out)

        violations = find_violations(final_text, facts, self.cart_url)
        if violations:
            log.warning("assistant answer rejected: %s", violations)
            text = facts_text(facts, "Вот что подтверждено данными каталога:")
            return RunResult(text, facts, "verified_fallback", violations, usage_in, usage_out)
        return RunResult(final_text, facts, "llm", [], usage_in, usage_out)
