"""Memory ingestion tasks."""

from __future__ import annotations

from celery_worker.app import app, run_async
from celery_worker.pipeline import store_batch
from shared.db import AsyncSessionLocal


@app.task(name="memory.process_batch")
def process_batch(
    clone_id: str,
    payload_size: int = 0,
    frames: int = 0,
    contents: list[str] | None = None,
    captured_at_unix_ms: list[int] | None = None,
) -> dict[str, str | int]:
    async def run() -> dict[str, str | int]:
        async with AsyncSessionLocal() as session:
            return await store_batch(
                session,
                clone_id,
                contents or [],
                captured_at_unix_ms or [],
            )

    result = run_async(run())
    result["frames"] = frames
    result["payload_size"] = payload_size
    return result
