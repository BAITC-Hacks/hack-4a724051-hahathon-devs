import asyncio
import json
import uuid
from dataclasses import dataclass

import httpx
import pytest

from app.adapters.ai_services.client import AIBudgetExceeded, AIServiceError, AIServicesClient, AIUsage
from app.contracts import ParsedDocument
from app.modules.documents.ocr import ImageReader, clean
from app.modules.search.semantic import SemanticIndex
from app.modules.search.service import MatchKind, SearchService
from tests.conftest import by_name


def client(handler, usage=None, provider="nvidia"):
    return AIServicesClient(provider, "key-test", usage, transport=httpx.MockTransport(handler))


def test_embed_request_and_order():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content), auth=request.headers["authorization"], url=str(request.url))
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})

    vectors = client(handler).embed(["a", "b"], "m", "passage")
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/embeddings"
    assert seen["auth"] == "Bearer key-test" and seen["input_type"] == "passage"


def test_errors_are_service_errors():
    for response in (httpx.Response(401), httpx.Response(302, headers={"location": "https://x.example"}),
                     httpx.Response(200, content=b"<html>"), httpx.Response(200, json={"data": []})):
        with pytest.raises(AIServiceError):
            client(lambda r, resp=response: resp).embed(["a"], "m", "query")


def test_read_image_sends_data_url():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "```\nАВ 16А | 10 шт\n```"}}]})

    text = client(handler).read_image(b"\xff\xd8", "image/jpeg", "vlm", "prompt")
    part = seen["messages"][0]["content"][1]
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert clean(text) == "АВ 16А | 10 шт"


def test_daily_budget_is_shared_and_enforced(tmp_path):
    usage = AIUsage(tmp_path / "s.sqlite3", site_daily_calls=3, session_daily_calls=2)
    ok = client(lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]}), usage)
    ok.embed(["a"], "m", "query", session_id="s1")
    ok.embed(["a"], "m", "query", session_id="s1")
    with pytest.raises(AIBudgetExceeded):
        ok.embed(["a"], "m", "query", session_id="s1")
    ok.embed(["a"], "m", "query", session_id="s2")
    other_process = AIUsage(tmp_path / "s.sqlite3", 3, 2)
    with pytest.raises(AIBudgetExceeded):
        other_process.reserve("s3")


class FakeVision:
    def __init__(self, texts):
        self.texts = list(texts)

    def read_image(self, image, media_type, model, prompt, max_tokens=2048, session_id=None):
        value = self.texts.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_image_reader_pages_and_failures():
    result = ImageReader(FakeVision(["стр 1", AIServiceError("x"), "стр 3"]), "m").read(
        [("image/png", b"1"), ("image/png", b"2"), ("image/png", b"3")])
    assert result.pages_read == 2 and result.pages_failed == 1
    assert "# Страница 1\nстр 1" in result.text and "стр 3" in result.text


class FakeEmbedder:
    """Вектор по словам: общий смысл = общие слова, чтобы тест не зависел от сети."""
    VOCAB = ["автомат", "выключатель", "16а", "розетка", "кабель", "свет", "бойлер"]

    def __init__(self):
        self.calls = 0

    def embed(self, texts, model, input_type, session_id=None):
        self.calls += 1
        out = []
        for t in texts:
            t = t.lower()
            vec = [1.0 if w in t else 0.0 for w in self.VOCAB]
            if "бойлер" in t:
                vec[0] = vec[1] = 1.0  # "автомат для бойлера" по смыслу про автоматы
            out.append(vec)
        return out


def test_semantic_index_roundtrip_and_cache(tmp_path, catalog):
    products = list(catalog._by_id.values())[:10]
    embedder = FakeEmbedder()
    index = SemanticIndex.build(products, embedder, "m")
    path = tmp_path / "idx.json"
    index.save(path)
    loaded = SemanticIndex.load(path, embedder)
    assert loaded.model == "m" and len(loaded.vectors) == 10
    loaded.search("автомат 16А")
    loaded.search("автомат 16А")
    assert embedder.calls == 2  # один раз на build, один на запрос (второй из кэша)


def test_semantic_results_are_fused_but_exact_article_wins(catalog):
    products = list(catalog._by_id.values())
    service = SearchService(catalog, SemanticIndex.build(products, FakeEmbedder(), "m", batch=64))
    service.semantic.client = FakeEmbedder()
    matches = service.search("что поставить на бойлер")
    assert matches and any(m.kind is MatchKind.SEMANTIC for m in matches)
    assert "выключатель" in matches[0].product.name.lower()
    exact = by_name(catalog, "RX3 1P 16А")
    assert service.search(exact.article)[0].product.id == exact.id


def test_semantic_failure_falls_back_to_text(catalog):
    class Broken:
        def embed(self, *a, **k):
            raise AIServiceError("down")

    index = SemanticIndex("m", {1: [1.0]}, Broken())
    plain = SearchService(catalog).search("розетка белая")
    assert [m.product.id for m in SearchService(catalog, index).search("розетка белая")] == [m.product.id for m in plain]


@dataclass
class Img:
    asset_id: uuid.UUID
    mime_type: str
    content: bytes


def test_runtime_reads_images_only_with_consent(tmp_path):
    from app.modules.assistant.processing import ProcessingContext
    from app.modules.assistant.runtime import GroundedAssistant
    from app.modules.chat.models import TurnInput

    asset = uuid.uuid4()

    class Docs:
        async def images(self, session_id, ids):
            return [Img(asset, "image/jpeg", b"x")]

    class Reader:
        def __init__(self):
            self.calls = 0

        async def aread(self, images, session_id=None):
            self.calls += 1
            from app.modules.documents.ocr import OcrResult
            return OcrResult("990100001_ | 2 шт", 1, 0)

    reader = Reader()
    assistant = GroundedAssistant(None, None, Docs(), tmp_path / "s.sqlite3", image_reader=reader)
    doc = ParsedDocument(asset_id=asset, status="ready", text="", warnings=[])
    for consent, expected_calls in ((False, 0), (True, 1)):
        ctx = ProcessingContext("s1", TurnInput(text="подбери", asset_ids=[asset], allow_external_analysis=consent),
                                (), (doc,))
        out = asyncio.run(assistant._read_images(ctx))
        assert reader.calls == expected_calls
    assert "990100001_ | 2 шт" in out.documents[0].text and "ocr_external" in out.documents[0].warnings


def test_openai_request_format():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    c = client(handler, provider="openai")
    c.embed(["a"], "text-embedding-3-small", "query")
    c.read_image(b"x", "image/png", "gpt-4.1-mini", "p")
    (emb_url, emb), (chat_url, chat) = seen
    assert emb_url == "https://api.openai.com/v1/embeddings"
    assert "input_type" not in emb and emb["dimensions"] == 512
    assert chat_url == "https://api.openai.com/v1/chat/completions"
    assert "max_completion_tokens" in chat and "max_tokens" not in chat


def test_settings_require_key_and_policy():
    from app.core.config import Settings
    with pytest.raises(ValueError):
        Settings(_env_file=None, semantic_search_enabled=True)
    with pytest.raises(ValueError):
        Settings(_env_file=None, ocr_enabled=True, llm_api_key="sk-x")
    ok = Settings(_env_file=None, ocr_enabled=True, llm_api_key="sk-x", llm_data_policy_accepted=True)
    assert ok.resolved_ocr_model() == "gpt-4.1-mini" and ok.resolved_embed_model() == "text-embedding-3-small"
    nv = Settings(_env_file=None, ai_services_provider="nvidia", semantic_search_enabled=True,
                  nvidia_api_key="nvapi-x", nvidia_data_policy_accepted=True)
    assert nv.ai_key() == "nvapi-x"
