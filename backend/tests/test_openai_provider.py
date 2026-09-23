import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

import httpx
import pytest

from app.adapters.llm.openai_provider import LlmUnavailable, OpenAIPlanningProvider
from app.modules.assistant.budget import (
    BudgetExceeded, BudgetUnavailable, CallAlreadyReserved, SessionExpired,
)
from app.modules.assistant.budget import DailyTokenBudget


PLAN = {
    "intent": "search", "query": "автомат 16 А", "queries": ["автомат 16 А"],
    "product_ids": [], "items": [], "topic_ids": [], "unresolved": [],
    "language": "ru", "clarification": "", "cart_requested": False,
}


def provider(tmp_path, client, *, session_budget=20000, site_budget=30000, model="gpt-4.1-mini"):
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
        db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", ("session-1", time.time() + 3600))
    return OpenAIPlanningProvider(
        db_path=tmp_path / "budget.sqlite3", api_key="test-key", model=model,
        timeout_s=1, max_output_tokens=300, session_budget=session_budget,
        site_budget=site_budget, client=client,
    )


def completed(plan=PLAN, *, usage=52):
    return {
        "status": "completed",
        "usage": {"input_tokens": 40, "output_tokens": 12,
                  "total_tokens": usage, "input_tokens_details": {"cached_tokens": 25}},
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(plan)},
        ]}],
    }


def test_responses_request_is_strict_stateless_and_accounts_all_usage(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        body = json.loads(request.content)
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "gpt-4.1-mini"
        assert body["store"] is False
        assert body["max_output_tokens"] == 300
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        assert body["input"][0]["role"] == "user"
        assert body["input"][0]["content"][0]["type"] == "input_text"
        assert body["input"][0]["content"][1]["type"] == "input_image"
        assert body["input"][0]["content"][1]["detail"] == "high"
        assert body["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json=completed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = provider(tmp_path, client)
            data = base64.b64encode(b"image bytes").decode()
            result = await adapter.plan(
                system="Select catalog intent only.", payload={"text": "автомат 16 А"},
                images=[{"media_type": "image/png", "data": data}],
                session_id="session-1", call_id="turn-1",
            )
            assert result == PLAN

    asyncio.run(run())
    assert len(seen) == 1
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        assert db.execute(
            "SELECT charged_tokens, state FROM llm_token_reservations WHERE call_id='turn-1'"
        ).fetchone() == (52, "completed")
        columns = {row[1] for row in db.execute("PRAGMA table_info(llm_token_reservations)")}
        assert columns == {"call_id", "day_utc", "session_id", "charged_tokens", "state"}


def test_sol_uses_reasoning_with_the_existing_grounded_output_contract(tmp_path):
    from app.core.config import Settings
    settings = Settings(_env_file=None, llm_enabled=True, llm_api_key="test-key", llm_data_policy_accepted=True)
    assert settings.llm_model == "gpt-6-sol"
    assert settings.worker_lease_seconds > settings.worker_timeout_seconds > settings.llm_timeout_seconds + 5
    assert settings.llm_max_output_tokens == 8192

    def handle(request):
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-sol"
        assert body["reasoning"] == {"effort": "medium"}
        assert body["text"]["format"]["strict"] is True
        assert body["store"] is False
        return httpx.Response(200, json=completed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = provider(tmp_path, client, model="gpt-6-sol")
            assert await adapter.plan(system="Select from known products.", payload={"text":"test"},
                                      images=[], session_id="session-1", call_id="sol-1") == PLAN
    asyncio.run(run())


def test_timeout_retains_reservation_and_blocks_replay(tmp_path):
    calls = 0

    def timeout(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            adapter = provider(tmp_path, client)
            kwargs = dict(system="Choose intent.", payload={"text": "test"}, images=[],
                          session_id="session-1", call_id="turn-1")
            with pytest.raises(LlmUnavailable):
                await adapter.plan(**kwargs)
            with pytest.raises(CallAlreadyReserved):
                await adapter.plan(**kwargs)

    asyncio.run(run())
    assert calls == 1
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        charged, state = db.execute(
            "SELECT charged_tokens, state FROM llm_token_reservations WHERE call_id='turn-1'"
        ).fetchone()
    assert charged > 300
    assert state == "reserved"


def test_budget_rejection_occurs_before_network(tmp_path):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = provider(tmp_path, client, session_budget=100, site_budget=100)
            with pytest.raises(BudgetExceeded):
                await adapter.plan(system="Choose intent.", payload={"text": "test"}, images=[],
                                   session_id="session-1", call_id="turn-1")

    asyncio.run(run())
    assert calls == 0


def test_invalid_plan_is_rejected_after_usage_is_accounted(tmp_path):
    invalid = {**PLAN, "intent": "freeform", "answer": "invented factual prose"}

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completed(invalid)))
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = provider(tmp_path, client)
            with pytest.raises(LlmUnavailable):
                await adapter.plan(system="Choose intent.", payload={"text": "test"}, images=[],
                                   session_id="session-1", call_id="turn-1")

    asyncio.run(run())
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        assert db.execute(
            "SELECT charged_tokens, state FROM llm_token_reservations WHERE call_id='turn-1'"
        ).fetchone() == (52, "completed")


def test_daily_site_budget_is_shared_across_instances(tmp_path):
    path = tmp_path / "shared.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
        db.executemany("INSERT INTO sessions VALUES (?, ?)", [
            ("first", time.time() + 3600), ("second", time.time() + 3600),
        ])
    one = DailyTokenBudget(path, session_limit=150, site_limit=150)
    two = DailyTokenBudget(path, session_limit=150, site_limit=150)
    one.reserve(call_id="one", session_id="first", tokens=100)
    with pytest.raises(BudgetExceeded):
        two.reserve(call_id="two", session_id="second", tokens=51)
    one.settle(call_id="one", actual_tokens=40)
    two.reserve(call_id="two", session_id="second", tokens=51)


def test_repeated_upstream_failures_open_circuit_before_network(tmp_path):
    calls = 0

    def fail(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "private upstream details"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            adapter = provider(tmp_path, client)
            for number in range(4):
                with pytest.raises(LlmUnavailable) as error:
                    await adapter.plan(
                        system="Choose intent.", payload={"text": "test"}, images=[],
                        session_id="session-1", call_id=f"turn-{number}",
                    )
                assert "private upstream details" not in str(error.value)

    asyncio.run(run())
    assert calls == 3
    with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM llm_token_reservations").fetchone()[0] == 3


def test_expired_session_cannot_reserve_or_call_upstream(tmp_path):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completed())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = provider(tmp_path, client)
            with sqlite3.connect(tmp_path / "budget.sqlite3") as db:
                db.execute("UPDATE sessions SET expires_at=? WHERE id=?", (time.time() - 1, "session-1"))
            with pytest.raises(SessionExpired):
                await adapter.plan(system="Choose intent.", payload={"text": "test"}, images=[],
                                   session_id="session-1", call_id="turn-1")

    asyncio.run(run())
    assert calls == 0


def test_concurrent_reservations_cannot_overdraw_site_budget(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, expires_at REAL NOT NULL)")
        db.executemany("INSERT INTO sessions VALUES (?, ?)", [
            ("first", time.time() + 3600), ("second", time.time() + 3600),
        ])
    budgets = [DailyTokenBudget(path, session_limit=100, site_limit=100) for _ in range(2)]

    def attempt(index):
        try:
            budgets[index].reserve(call_id=str(index), session_id=("first", "second")[index],
                                   tokens=70)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COALESCE(SUM(charged_tokens), 0) FROM llm_token_reservations").fetchone()[0] == 70


def test_missing_session_store_fails_closed(tmp_path):
    budget = DailyTokenBudget(tmp_path / "uninitialized.sqlite3", session_limit=100, site_limit=100)
    with pytest.raises(BudgetUnavailable):
        budget.reserve(call_id="one", session_id="unknown", tokens=10)
