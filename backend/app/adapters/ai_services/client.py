"""Клиент внешних ИИ-сервисов для распознавания изображений и эмбеддингов.

Провайдеры: OpenAI (api.openai.com) и NVIDIA API Catalog (build.nvidia.com). У обоих
формат OpenAI (/chat/completions, /embeddings), отличия учтены ниже. Адреса
фиксированы в коде, ключ только из окружения. Без редиректов, с таймаутами и без
автоповторов платных запросов. Каждый вызов сначала списывает дневной лимит в
AIUsage, поэтому API и worker вместе не выходят за бюджет.
"""

import base64
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URLS = {"openai": "https://api.openai.com/v1", "nvidia": "https://integrate.api.nvidia.com/v1"}
# Размер вектора у OpenAI text-embedding-3 можно сократить без заметной потери качества.
OPENAI_EMBED_DIMENSIONS = 512
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class AIServiceError(Exception):
    """Сервис недоступен, отказал или вернул неожиданный ответ. Вызывающий код продолжает без него."""


class AIBudgetExceeded(AIServiceError):
    pass


class AIUsage:
    """Дневные лимиты вызовов в общей SQLite-базе состояния (одна на API и worker)."""

    def __init__(self, db_path: Path, site_daily_calls: int, session_daily_calls: int):
        self.db_path = Path(db_path)
        self.site_daily_calls = site_daily_calls
        self.session_daily_calls = session_daily_calls
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS ai_service_usage (day TEXT NOT NULL, scope TEXT NOT NULL, "
                       "calls INTEGER NOT NULL, PRIMARY KEY (day, scope))")

    def reserve(self, session_id: str | None, calls: int = 1) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scopes = [("site", self.site_daily_calls)]
        if session_id:
            scopes.append((f"session:{session_id}", self.session_daily_calls))
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for scope, limit in scopes:
                row = db.execute("SELECT calls FROM ai_service_usage WHERE day=? AND scope=?", (day, scope)).fetchone()
                if (row[0] if row else 0) + calls > limit:
                    raise AIBudgetExceeded(scope)
            for scope, _ in scopes:
                db.execute("INSERT INTO ai_service_usage VALUES (?, ?, ?) ON CONFLICT(day, scope) "
                           "DO UPDATE SET calls = calls + excluded.calls", (day, scope, calls))


class AIServicesClient:
    def __init__(self, provider: str, api_key: str, usage: AIUsage | None = None, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        if provider not in BASE_URLS:
            raise ValueError(f"unknown provider {provider}")
        if not api_key:
            raise ValueError(f"API key for {provider} is not set")
        self.provider = provider
        self.usage = usage
        self._http = httpx.Client(
            base_url=BASE_URLS[provider], timeout=httpx.Timeout(timeout_s, connect=5.0), follow_redirects=False,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}, transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, body: dict, session_id: str | None) -> dict:
        if self.usage is not None:
            self.usage.reserve(session_id)
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as e:
            raise AIServiceError(type(e).__name__) from e
        if response.status_code != 200:
            raise AIServiceError(f"HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise AIServiceError("response_too_large")
        try:
            data = response.json()
        except ValueError as e:
            raise AIServiceError("invalid_json") from e
        if not isinstance(data, dict):
            raise AIServiceError("bad_response")
        return data

    def embed(self, texts: list[str], model: str, input_type: str, session_id: str | None = None) -> list[list[float]]:
        """input_type: "query" для запроса клиента, "passage" для карточек каталога."""
        if not texts:
            return []
        body = {"model": model, "input": texts, "encoding_format": "float"}
        if self.provider == "nvidia":
            body.update(input_type=input_type, truncate="END")  # у NVIDIA запрос и карточка кодируются по-разному
        elif model.startswith("text-embedding-3"):
            body["dimensions"] = OPENAI_EMBED_DIMENSIONS
        data = self._post("/embeddings", body, session_id)
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise AIServiceError("bad_embeddings")
        vectors = [None] * len(texts)
        for item in items:
            index, vector = item.get("index"), item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < len(texts) or not isinstance(vector, list):
                raise AIServiceError("bad_embeddings")
            vectors[index] = [float(x) for x in vector]
        return vectors

    def read_image(self, image: bytes, media_type: str, model: str, prompt: str, max_tokens: int = 2048,
                   session_id: str | None = None) -> str:
        """Текст с изображения через модель, понимающую картинки (chat completions)."""
        limit = {"max_completion_tokens" if self.provider == "openai" else "max_tokens": max_tokens}
        data = self._post("/chat/completions", {
            "model": model, **limit, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"}},
            ]}],
        }, session_id)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIServiceError("bad_completion") from e
        if not isinstance(text, str):
            raise AIServiceError("bad_completion")
        return text
