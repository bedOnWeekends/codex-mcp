from __future__ import annotations

import asyncio
import logging

from .codex_worker import CodexWorker
from .database import create_database
from .settings import get_settings
from .store import Store


async def worker_loop() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper()))
    settings.ensure_runtime_directories()
    database = create_database(settings)
    worker = CodexWorker(Store(database.session_factory), settings)
    try:
        while True:
            processed = await worker.process_one()
            if not processed:
                await asyncio.sleep(settings.worker_poll_interval_seconds)
    finally:
        await database.close()


def run() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
