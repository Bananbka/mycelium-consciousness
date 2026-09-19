from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from shared import backup_codec, streams
from shared.db.models import MemoryBuffer

logger = logging.getLogger(__name__)


async def flush_one_stream(session: AsyncSession, clone_id: int) -> dict[str, int]:
    entries = await streams.read_all(clone_id)
    if not entries:
        return {"clone_id": clone_id, "flushed": 0}

    new_items = [
        {"content": fields["content"], "captured_at": fields["captured_at"]}
        for _, fields in entries
    ]

    buffer = await session.get(MemoryBuffer, clone_id, with_for_update=True)
    existing_items = buffer.payload if buffer is not None else []
    if buffer is None:
        buffer = MemoryBuffer(clone_id=clone_id, payload_blob=b"", entry_count=0)
        session.add(buffer)

    combined = existing_items + new_items
    buffer.payload_blob = backup_codec.encode_payload(combined)
    buffer.entry_count = len(combined)
    await session.commit()

    last_entry_id = entries[-1][0]
    try:
        await streams.trim_up_to(clone_id, last_entry_id)
    except Exception:
        logger.exception(
            "trim failed after flush commit clone_id=%d, reverting the buffer "
            "to its pre-flush contents so the next flush can retry cleanly",
            clone_id,
        )
        buffer.payload_blob = backup_codec.encode_payload(existing_items)
        buffer.entry_count = len(existing_items)
        await session.commit()
        raise

    return {"clone_id": clone_id, "flushed": len(new_items)}


async def flush_all_streams(session: AsyncSession) -> list[dict[str, int]]:
    results = []
    for clone_id in await streams.active_clone_ids():
        try:
            results.append(await flush_one_stream(session, clone_id))
        except Exception:
            logger.exception("flush failed for clone_id=%d, skipping", clone_id)
            await session.rollback()
    return results
