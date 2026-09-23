import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from app.adapters.storage.sqlite import SQLiteStateStore
from app.core.errors import AppError
from app.modules.chat.models import AssistantOutput, TurnInput
from app.worker import Worker


def prepare(services):
    store = services.store
    store.initialize()
    now = time.time()
    session = store.create_session("token-digest", now + 1000)
    conv = store.create_conversation(session.id, now, 20)
    turn = store.submit(session.id, str(conv.id), "request-0001", TurnInput(text="hello"), now, 100, 200)
    return session, conv, turn.turn


def test_restart_preserves_queued_work(services):
    session, conv, turn = prepare(services)
    reopened = SQLiteStateStore(services.settings.local_db_path)
    reopened.initialize()
    assert reopened.get_turn(session.id, str(turn.id)).status == "queued"
    assert reopened.claim(time.time(), 60).turn_id == str(turn.id)


def test_atomic_idempotency_under_concurrent_requests(services):
    store = services.store
    store.initialize()
    now = time.time()
    session = store.create_session("hash", now + 1000)
    conv = store.create_conversation(session.id, now, 20)

    def send(_):
        return store.submit(session.id, str(conv.id), "same-key", TurnInput(text="x"), now, 100, 200)

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(send, range(6)))
    assert sum(item.created for item in results) == 1
    assert len({item.turn.id for item in results}) == 1
    assert len(store.messages(session.id, str(conv.id), 50)) == 1


def test_only_one_worker_claims_job(services):
    prepare(services)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: services.store.claim(time.time(), 60), range(2)))
    assert sum(item is not None for item in results) == 1


def test_expired_lease_cannot_publish_or_replay(services):
    session, conv, turn = prepare(services)
    now = time.time()
    job = services.store.claim(now, 1)
    assert services.store.claim(now + 2, 60) is None
    assert services.store.finish(job, AssistantOutput(message="late", mode="unavailable"), None, now + 2) is False
    result = services.store.get_turn(session.id, str(turn.id))
    assert result.status == "failed"
    assert result.error_code == "execution_interrupted"


def test_cancellation_fences_inflight_result(services):
    session, conv, turn = prepare(services)
    job = services.store.claim(time.time(), 60)
    services.store.cancel(session.id, str(turn.id), time.time())
    assert services.store.finish(job, AssistantOutput(message="late", mode="unavailable"), None, time.time()) is False
    assert len(services.store.messages(session.id, str(conv.id), 50)) == 1


def test_session_expiry_cascades_private_state(services):
    session, conv, turn = prepare(services)
    services.store.cleanup(session.expires_at + 1)
    assert not services.store.session_alive(session.id, session.expires_at + 1)
    assert services.store.claim(session.expires_at + 1, 60) is None


def test_processor_exception_is_sanitized(services):
    session, conv, turn = prepare(services)

    class BrokenProcessor:
        async def process(self, context):
            raise RuntimeError("SECRET-DO-NOT-PUBLISH")

    worker = Worker(services.store, BrokenProcessor(), services.documents, services.settings)
    assert asyncio.run(worker.run_once())
    result = services.store.get_turn(session.id, str(turn.id))
    assert result.error_code == "processing_failed"
    assert "SECRET" not in result.model_dump_json()


def test_worker_timeout_is_terminal_without_retry(services):
    session, conv, turn = prepare(services)

    class SlowProcessor:
        async def process(self, context):
            await asyncio.sleep(1)

    settings = services.settings.model_copy(update={"worker_timeout_seconds": 0.1})
    worker = Worker(services.store, SlowProcessor(), services.documents, settings)
    asyncio.run(worker.run_once())
    assert services.store.get_turn(session.id, str(turn.id)).error_code == "processing_timeout"
    assert asyncio.run(worker.run_once()) is False
