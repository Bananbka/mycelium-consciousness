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
    owner_user_id: int,
    payloads: list[bytes],
    captured_at_unix_ms: list[int] | None = None,
) -> dict[str, str | int]:
    """Resolve the clone, embed each frame, and persist the chunks."""
    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.user_id == owner_user_id)
    )

    if profile is None:
        logger.warning(
            "rejecting batch: no clone profile linked to user_id=%r", owner_user_id
        )
        return {
            "owner_user_id": owner_user_id,
            "stored": 0,
            "status": "no_clone_profile",
        }

    timestamps = captured_at_unix_ms or []
    embedder = get_embedder()
    stored = 0

    for index, payload in enumerate(payloads):
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "dropping frame %d for user_id=%r: not valid UTF-8 (%d bytes)",
                index,
                owner_user_id,
                len(payload),
            )
            continue

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
    return {"owner_user_id": owner_user_id, "stored": stored, "status": "stored"}
