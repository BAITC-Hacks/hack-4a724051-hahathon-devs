"""Stateless OpenAI Responses planning adapter; server code owns factual rendering."""

import base64
import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.errors import AppError
from app.modules.assistant.budget import DailyTokenBudget


class LlmUnavailable(AppError):
    def __init__(self):
        super().__init__("llm_unavailable", "Модель временно недоступна.", 503)


ProductId = Annotated[int, Field(gt=0, strict=True)]
ShortQuery = Annotated[str, Field(max_length=200)]
TopicId = Annotated[str, Field(max_length=80)]
Unresolved = Annotated[str, Field(max_length=120)]


class PlannedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product_id: ProductId
    quantity: Annotated[int, Field(gt=0, strict=True)]


class SelectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: Literal["search", "product", "alternatives", "terms", "propose", "clarify"]
    query: ShortQuery
    queries: list[ShortQuery] = Field(max_length=20)
    product_ids: list[ProductId] = Field(max_length=8)
    items: list[PlannedItem] = Field(max_length=20)
    topic_ids: list[TopicId] = Field(max_length=8)
    unresolved: list[Unresolved] = Field(max_length=10)
    language: Literal["ru", "kk", "en"]
    clarification: str = Field(max_length=500)
    cart_requested: bool


PLAN_SCHEMA = SelectionPlan.model_json_schema()


class OpenAIPlanningProvider:
    def __init__(
        self, *, db_path: Path, api_key: str, model: str, timeout_s: float,
        max_output_tokens: int, session_budget: int, site_budget: int,
        image_token_reserve: int = 8192,
        failure_threshold: int = 3, circuit_cooldown_s: float = 60,
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key or not model or timeout_s <= 0 or max_output_tokens <= 0:
            raise ValueError("OpenAI provider requires key, model, timeout and output limit")
        if image_token_reserve <= 0 or failure_threshold <= 0 or circuit_cooldown_s <= 0:
            raise ValueError("Image reserve and circuit limits must be positive")
        if base_url != "https://api.openai.com/v1" and client is None:
            raise ValueError("Custom API URL is only available with an injected client")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.image_token_reserve = image_token_reserve
        self.failure_threshold = failure_threshold
        self.circuit_cooldown_s = circuit_cooldown_s
        self._failures = 0
        self._opened_until = 0.0
        self.base_url = base_url.rstrip("/")
        self.budget = DailyTokenBudget(
            db_path, session_limit=session_budget, site_limit=site_budget,
        )
        self._client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _mark_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_until = time.monotonic() + self.circuit_cooldown_s

    def _mark_success(self) -> None:
        self._failures = 0
        self._opened_until = 0.0

    @staticmethod
    def _image_part(image: dict) -> dict:
        if not isinstance(image, dict):
            raise LlmUnavailable()
        media_type = image.get("media_type")
        data = image.get("data")
        if media_type not in {"image/png", "image/jpeg", "image/webp"} or not isinstance(data, str):
            raise LlmUnavailable()
        if len(data) > 14_000_000:
            raise LlmUnavailable()
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, base64.binascii.Error):
            raise LlmUnavailable() from None
        if not decoded or len(decoded) > 10_000_000:
            raise LlmUnavailable()
        return {
            "type": "input_image", "detail": "high",
            "image_url": f"data:{media_type};base64,{data}",
        }

    @staticmethod
    def _reported_usage(response: dict) -> int | None:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        value = usage.get("total_tokens")
        if type(value) is int and value >= 0:
            return value
        inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
        if type(inputs) is int and type(outputs) is int and inputs >= 0 and outputs >= 0:
            return inputs + outputs
        return None

    async def plan(
        self, *, system: str, payload: dict, images: list[dict],
        session_id: str, call_id: str,
    ) -> dict:
        if not isinstance(system, str) or not isinstance(payload, dict) or not isinstance(images, list):
            raise LlmUnavailable()
        if len(images) > 4:
            raise LlmUnavailable()
        try:
            user_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise LlmUnavailable() from None
        if len(user_json.encode("utf-8")) > 128_000 or len(system.encode("utf-8")) > 32_000:
            raise LlmUnavailable()
        content = [{"type": "input_text", "text": user_json}]
        content.extend(self._image_part(image) for image in images)
        if time.monotonic() < self._opened_until:
            raise LlmUnavailable()
        # One UTF-8 byte per text token is a conservative upper estimate. Images
        # at high detail get a separately configurable worst-case reservation.
        reserve = (len(system.encode("utf-8")) + len(user_json.encode("utf-8"))
                   + len(json.dumps(PLAN_SCHEMA).encode("utf-8")) + 4096
                   + len(images) * self.image_token_reserve + self.max_output_tokens)
        await asyncio.to_thread(self.budget.reserve, call_id=call_id, session_id=session_id, tokens=reserve)
        request_body = {
            "model": self.model,
            "instructions": system,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {
                "type": "json_schema", "name": "selection_plan", "strict": True,
                "schema": PLAN_SCHEMA,
            }},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        try:
            upstream = await self._client.post(
                self.base_url + "/responses", json=request_body,
                headers={"Authorization": "Bearer " + self.api_key}, timeout=self.timeout_s,
            )
            if upstream.status_code != 200:
                raise LlmUnavailable()
            response = upstream.json()
            if not isinstance(response, dict):
                raise LlmUnavailable()
            actual = self._reported_usage(response)
            if actual is not None:
                await asyncio.to_thread(self.budget.settle, call_id=call_id, actual_tokens=actual)
            if response.get("status") != "completed":
                raise LlmUnavailable()
            texts = [part.get("text") for item in response.get("output", [])
                     if isinstance(item, dict) and item.get("type") == "message"
                     for part in item.get("content", [])
                     if isinstance(part, dict) and part.get("type") == "output_text"]
            if len(texts) != 1 or not isinstance(texts[0], str):
                raise LlmUnavailable()
            plan = SelectionPlan.model_validate_json(texts[0])
            self._mark_success()
            return plan.model_dump()
        except LlmUnavailable:
            self._mark_failure()
            raise
        except (httpx.HTTPError, json.JSONDecodeError, ValidationError, TypeError, KeyError):
            self._mark_failure()
            raise LlmUnavailable() from None
