import argparse
import asyncio
import logging
import time

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.assistant.processing import AssistantProcessor, ProcessingContext
from app.modules.chat.ports import StateStore
from app.modules.chat.models import AssistantOutput
from app.modules.chat.application import resolve_documents

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store: StateStore, processor: AssistantProcessor, documents, settings: Settings):
        self.store, self.processor = store, processor
        self.documents, self.settings = documents, settings

    async def run_once(self) -> bool:
        if hasattr(self.documents, "cleanup"):
            await self.documents.cleanup()
        await asyncio.to_thread(self.store.cleanup, time.time())
        job = await asyncio.to_thread(
            self.store.claim, time.time(), self.settings.worker_lease_seconds,
        )
        if job is None:
            return False
        output, error_code = None, None
        try:
            async with asyncio.timeout(self.settings.worker_timeout_seconds):
                if not await asyncio.to_thread(self.store.active, job, time.time()):
                    return True
                history = await asyncio.to_thread(
                    self.store.messages, job.session_id, job.conversation_id, 20,
                )
                bounded, remaining = [], 32768
                for message in reversed(history):
                    size = len(message.text.encode("utf-8"))
                    if size > remaining:
                        break
                    bounded.append(message)
                    remaining -= size
                documents = await resolve_documents(
                    self.documents, job.session_id, [str(x) for x in job.payload.asset_ids],
                )
                if not await asyncio.to_thread(self.store.active, job, time.time()):
                    return True
                output = await self.processor.process(ProcessingContext(
                    job.session_id, job.payload, tuple(reversed(bounded)), tuple(documents),
                    job.turn_id, job.conversation_id,
                ))
                output = AssistantOutput.model_validate(output)
        except TimeoutError:
            error_code = "processing_timeout"
        except AppError as error:
            error_code = error.code
        except Exception:
            # Do not log exception text: provider/parser errors may contain private content.
            logger.error("Processing failed for turn %s", job.turn_id)
            error_code = "processing_failed"
        await asyncio.to_thread(self.store.finish, job, output, error_code, time.time())
        return True

    async def run_forever(self):
        while True:
            try:
                processed = await self.run_once()
            except Exception:
                logger.error("Worker storage unavailable")
                processed = False
            if not processed:
                await asyncio.sleep(self.settings.worker_poll_seconds)


async def run_container(container, *, once: bool = False):
    """Keep initialization, execution and cleanup within the same event loop."""
    try:
        await asyncio.to_thread(container.store.initialize)
        if once:
            return await container.worker.run_once()
        await container.worker.run_forever()
    finally:
        await container.aclose()


def main():
    from app.bootstrap import build_container
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    container = build_container(Settings())
    asyncio.run(run_container(container, once=args.once))


if __name__ == "__main__":
    main()
