from __future__ import annotations

import asyncio
import os

from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
TASK_NAME = "memory.process_batch"
TASK_QUEUE = os.getenv("CELERY_TASK_QUEUE", "memory_batches")

_client = Celery("clone_memory_ingester", broker=BROKER_URL)


async def dispatch_batch(
    clone_id: str,
    payload_size: int,
    frames: int,
    contents: list[str] | None = None,
    captured_at_unix_ms: list[int] | None = None,
) -> None:
    await asyncio.to_thread(
        _client.send_task,
        TASK_NAME,
        kwargs={
            "clone_id": clone_id,
            "payload_size": payload_size,
            "frames": frames,
            "contents": contents or [],
            "captured_at_unix_ms": captured_at_unix_ms or [],
        },
        queue=TASK_QUEUE,
    )
