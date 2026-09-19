from __future__ import annotations

from datetime import UTC, datetime

from celery_worker.app import app, run_async
from celery_worker.flush import flush_all_streams, flush_one_stream
from celery_worker.pipeline import rollup_clone, run_rollup
from shared.db import AsyncSessionLocal
from shared.db.models import CloneProfile


@app.task(name="memory.flush_due_streams")
def flush_due_streams() -> list[dict[str, int]]:
    async def run() -> list[dict[str, int]]:
        async with AsyncSessionLocal() as session:
            return await flush_all_streams(session)

    return run_async(run())


@app.task(name="memory.flush_clone_stream")
def flush_clone_stream(clone_id: int) -> dict[str, int]:
    async def run() -> dict[str, int]:
        async with AsyncSessionLocal() as session:
            return await flush_one_stream(session, clone_id)

    return run_async(run())


@app.task(name="memory.rollup_due_clones")
def rollup_due_clones() -> list[dict[str, str | int]]:
    async def run() -> list[dict[str, str | int]]:
        async with AsyncSessionLocal() as session:
            return await run_rollup(session)

    return run_async(run())


@app.task(name="memory.force_rollup_clone")
def force_rollup_clone(clone_id: int) -> dict[str, str | int]:
    """Roll up one clone right now, ignoring its tier's due date.

    For testing and ops use (e.g. before a risky change to a clone), triggered
    from the API's admin router rather than celery-beat's schedule.
    """

    async def run() -> dict[str, str | int]:
        async with AsyncSessionLocal() as session:
            clone = await session.get(CloneProfile, clone_id)
            if clone is None:
                return {"clone_id": clone_id, "status": "not_found", "entry_count": 0}
            return await rollup_clone(session, clone, datetime.now(UTC))

    return run_async(run())
