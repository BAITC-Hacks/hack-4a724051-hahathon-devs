"""Клиент NVIDIA API Catalog (build.nvidia.com): эмбеддинги и распознавание изображений.

Адрес фиксирован в коде, ключ только из окружения (NVIDIA_API_KEY). Без редиректов,
с таймаутами и без автоповторов платных запросов. Каждый вызов сначала списывает
лимит в NvidiaUsage, поэтому API и worker вместе не выходят за дневной бюджет.
"""

import base64
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_URL = "https://integrate.api.nvidia.com/v1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class NvidiaError(Exception):
    """Сервис недоступен, отказал или вернул неожиданный ответ. Вызывающий код продолжает без него."""


class NvidiaBudgetExceeded(NvidiaError):
    pass


class NvidiaUsage:
    """Дневные лимиты вызовов в общей SQLite-базе состояния (одна на API и worker)."""

    def __init__(self, db_path: Path, site_daily_calls: int, session_daily_calls: int):
        self.db_path = Path(db_path)
        self.site_daily_calls = site_daily_calls
        self.session_daily_calls = session_daily_calls
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS nvidia_usage (day TEXT NOT NULL, scope TEXT NOT NULL, "
                       "calls INTEGER NOT NULL, PRIMARY KEY (day, scope))")

    def reserve(self, session_id: str | None, calls: int = 1) -> None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scopes = [("site", self.site_daily_calls)]
        if session_id:
            scopes.append((f"session:{session_id}", self.session_daily_calls))
        with closing(sqlite3.connect(self.db_path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for scope, limit in scopes:
                row = db.execute("SELECT calls FROM nvidia_usage WHERE day=? AND scope=?", (day, scope)).fetchone()
                if (row[0] if row else 0) + calls > limit:
                    raise NvidiaBudgetExceeded(scope)
            for scope, _ in scopes:
                db.execute("INSERT INTO nvidia_usage VALUES (?, ?, ?) ON CONFLICT(day, scope) "
                           "DO UPDATE SET calls = calls + excluded.calls", (day, scope, calls))


class NvidiaClient:
    def __init__(self, api_key: str, usage: NvidiaUsage | None = None, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("NVIDIA_API_KEY не задан")
        self.usage = usage
        self._http = httpx.Client(
            base_url=BASE_URL, timeout=httpx.Timeout(timeout_s, connect=5.0), follow_redirects=False,
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
            raise NvidiaError(type(e).__name__) from e
        if response.status_code != 200:
            raise NvidiaError(f"HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise NvidiaError("response_too_large")
        try:
            data = response.json()
        except ValueError as e:
            raise NvidiaError("invalid_json") from e
        if not isinstance(data, dict):
            raise NvidiaError("bad_response")
        return data

    def embed(self, texts: list[str], model: str, input_type: str, session_id: str | None = None) -> list[list[float]]:
        """input_type: "query" для запроса клиента, "passage" для карточек каталога."""
        if not texts:
            return []
        data = self._post("/embeddings", {
            "model": model, "input": texts, "input_type": input_type,
            "encoding_format": "float", "truncate": "END",
        }, session_id)
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise NvidiaError("bad_embeddings")
        vectors = [None] * len(texts)
        for item in items:
            index, vector = item.get("index"), item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < len(texts) or not isinstance(vector, list):
                raise NvidiaError("bad_embeddings")
            vectors[index] = [float(x) for x in vector]
        return vectors

    def read_image(self, image: bytes, media_type: str, model: str, prompt: str, max_tokens: int = 2048,
                   session_id: str | None = None) -> str:
        """Текст с изображения через модель, понимающую картинки (chat completions)."""
        data = self._post("/chat/completions", {
            "model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{base64.b64encode(image).decode('ascii')}"}},
            ]}],
        }, session_id)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise NvidiaError("bad_completion") from e
        if not isinstance(text, str):
            raise NvidiaError("bad_completion")
        return text
