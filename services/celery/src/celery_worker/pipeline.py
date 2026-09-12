"""Business logic for memory ingestion.

Kept free of Celery imports so it can be exercised directly in tests against a
transaction that gets rolled back.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import CloneProfile, MemoryChunk
from shared.embeddings import get_embedder

logger = logging.getLogger(__name__)


def _captured_at(unix_ms: int | None) -> datetime | None:
    """Convert an implant capture time to a datetime, ignoring unset values."""
    if not unix_ms or unix_ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(unix_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        logger.warning("ignoring out-of-range captured_at_unix_ms=%r", unix_ms)
        return None


async def store_batch(
    session: AsyncSession,
    clone_designation: str,
    contents: list[str],
    captured_at_unix_ms: list[int] | None = None,
) -> dict[str, str | int]:
    """Resolve the clone, embed each frame, and persist the chunks."""
    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.designation == clone_designation)
    )

    if profile is None:
        # Never auto-create: the stream is unauthenticated, so an unknown
        # designation must not be able to conjure a clone registry entry.
        logger.warning("rejecting batch for unknown clone %r", clone_designation)
        return {
            "clone_id": clone_designation,
            "stored": 0,
            "status": "rejected_unknown_clone",
        }

    timestamps = captured_at_unix_ms or []
    embedder = get_embedder()
    stored = 0

    for index, content in enumerate(contents):
        text = content.strip()
        if not text:
            continue

        chunk = MemoryChunk(
            clone_id=profile.id,
            content=text,
            embedding=await embedder.embed(text),
        )
        # Record when the implant captured the memory, not when the worker
        # happened to insert it; falls back to the column's server default.
        captured = _captured_at(timestamps[index] if index < len(timestamps) else None)
        if captured is not None:
            chunk.timestamp = captured

        session.add(chunk)
        stored += 1

    await session.commit()
    return {"clone_id": clone_designation, "stored": stored, "status": "stored"}
