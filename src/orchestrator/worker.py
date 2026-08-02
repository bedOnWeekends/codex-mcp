from __future__ import annotations

import asyncio
import logging

from .codex_worker import CodexWorker
from .database import create_database
from .phase5_store import Phase5Store
from .settings import get_settings

logger = logging.getLogger(__name__)


async def _worker_loop(worker: CodexWorker, *, worker_name: str) -> None:
    logger.info("worker loop started", extra={"worker_name": worker_name})
    while True:
        processed = await worker.process_one()
        if not processed:
            await asyncio.sleep(worker.settings.worker_poll_interval_seconds)


async def worker_loop() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings.ensure_runtime_directories()
    database = create_database(settings)
    store = Phase5Store(database.session_factory)
    workers = [
        CodexWorker(store, settings) for _ in range(settings.max_parallel_workers)
    ]
    try:
        async with asyncio.TaskGroup() as group:
            for index, worker in enumerate(workers, start=1):
                group.create_task(
                    _worker_loop(
                        worker,
                        worker_name=f"{settings.worker_id}-{index}",
                    )
                )
    finally:
        await database.close()


def run() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
