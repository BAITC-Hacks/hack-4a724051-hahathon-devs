"""Клиент API каталога ekt.kz. Только GET к двум фиксированным путям.

Адрес задаётся конфигурацией сервера, логин и пароль только из окружения.
Ошибки источника не превращаются в пустой каталог или нулевой остаток:
вызывающий код получает исключение и сам решает, что показать.
"""

import json
import random
import time

import httpx

MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class EktError(Exception):
    """Базовая ошибка источника."""


class EktAuthError(EktError):
    """401/403: неверные логин/пароль. Повторять бесполезно."""


class EktNotFound(EktError):
    pass


class EktUnavailable(EktError):
    """Таймаут, 429, 5xx или обрыв соединения."""


class EktSchemaError(EktError):
    """Ответ 200, но структура не та, что ожидаем."""


class EktClient:
    def __init__(self, user: str, password: str, base_url: str = "https://ekt.kz",
                 timeout_s: float = 5.0, transport: httpx.BaseTransport | None = None):
        if not user or not password:
            raise ValueError("EKT_API_USER и EKT_API_PASSWORD не заданы")
        self._http = httpx.Client(
            base_url=base_url, auth=httpx.BasicAuth(user, password),
            timeout=httpx.Timeout(timeout_s, connect=2.0), follow_redirects=False,
            headers={"Accept": "application/json"}, transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict) -> dict:
        last_error: EktError | None = None
        for attempt in range(2):  # для GET допустим один повтор
            if attempt:
                time.sleep(0.3 + random.random() * 0.4)
            try:
                with self._http.stream("GET", path, params=params) as response:
                    if response.status_code in (401, 403):
                        raise EktAuthError(f"HTTP {response.status_code}")
                    if response.status_code == 404:
                        raise EktNotFound(path)
                    if response.status_code == 429 or response.status_code >= 500:
                        last_error = EktUnavailable(f"HTTP {response.status_code}")
                        continue
                    if response.status_code != 200:
                        raise EktSchemaError(f"HTTP {response.status_code}")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise EktSchemaError("response_too_large")
            except httpx.TransportError as e:
                last_error = EktUnavailable(type(e).__name__)
                continue
            try:
                data = json.loads(body)
            except ValueError as e:
                raise EktSchemaError("invalid_json") from e
            if not isinstance(data, dict):
                raise EktSchemaError("not_an_object")
            return data
        raise last_error or EktUnavailable("unknown")

    def list_page(self, page: int) -> dict:
        if page < 1:
            raise ValueError("page >= 1")
        data = self._get("/api/products", {"page": page})
        items = data.get("items")
        if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("id"), int)
                                                  for i in items):
            raise EktSchemaError("bad_page")
        return data

    def get_detail(self, product_id: int) -> dict:
        if product_id < 1:
            raise ValueError("product_id >= 1")
        data = self._get("/api/products/detail", {"id": product_id})
        if data.get("id") != product_id or not isinstance(data.get("name"), str):
            raise EktSchemaError("bad_detail")
        if "stores" in data and not isinstance(data["stores"], list):
            raise EktSchemaError("bad_stores")
        return data
