from types import SimpleNamespace

from app.adapters.llm.anthropic_provider import AnthropicProvider
from app.modules.assistant.ports import Message, Part, ToolResult, ToolSpec


def fake_response(blocks, stop="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_request_shape_and_tool_parsing():
    provider = AnthropicProvider(api_key="test", model="claude-opus-5")
    captured = {}
    tool_block = SimpleNamespace(type="tool_use", id="t1", name="get_product", input={"product_id": 5})

    def create(**kwargs):
        captured.update(kwargs)
        return fake_response([SimpleNamespace(type="thinking", thinking=""), tool_block])

    provider.client.beta.messages.create = create
    spec = ToolSpec("get_product", "d", {"type": "object", "properties": {}})
    turn = provider.generate("sys", [Message("user", parts=[Part("text", text="привет"),
                                                            Part("image", media_type="image/jpeg", data="AAA")])],
                             [spec])

    assert captured["model"] == "claude-opus-5"
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["fallbacks"] == "default"
    assert captured["betas"] == ["server-side-fallback-2026-07-01"]
    assert captured["messages"][0]["content"][1]["source"]["type"] == "base64"
    assert turn.stop == "tool_use"
    assert turn.tool_calls[0].input == {"product_id": 5}
    # ответ модели уходит обратно без изменений, вместе с thinking
    assert turn.message.raw[0].type == "thinking"


def test_tool_results_go_first_in_user_message():
    provider = AnthropicProvider(api_key="test", model="m")
    captured = {}
    provider.client.beta.messages.create = lambda **kw: captured.update(kw) or fake_response(
        [SimpleNamespace(type="text", text="ок")], stop="end_turn")
    raw = [SimpleNamespace(type="tool_use", id="t1", name="x", input={})]
    turn = provider.generate("sys", [
        Message("user", parts=[Part("text", text="q")]),
        Message("assistant", raw=raw),
        Message("user", tool_results=[ToolResult("t1", "{}")]),
    ], [])
    assert captured["messages"][1]["content"] is raw
    assert captured["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "{}",
                                                     "is_error": False}
    assert turn.text == "ок" and turn.stop == "end"
