from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared import object_storage
from shared.db import CloneProfile, MemoryBackup, MemoryBuffer
from shared.subscriptions import BACKUP_POLICIES

logger = logging.getLogger(__name__)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cutoff_by_tier(now: datetime):
    return case(
        *[
            (CloneProfile.subscription_tier == tier, now - policy.interval)
            for tier, policy in BACKUP_POLICIES.items()
        ]
    )


async def find_due_clones(session: AsyncSession, now: datetime) -> list[CloneProfile]:
    last_backup = (
        select(
            MemoryBackup.clone_id,
            func.max(MemoryBackup.period_end).label("last_end"),
        )
        .group_by(MemoryBackup.clone_id)
        .subquery()
    )
    anchor = func.coalesce(last_backup.c.last_end, CloneProfile.created_at)

    result = await session.execute(
        select(CloneProfile)
        .outerjoin(last_backup, last_backup.c.clone_id == CloneProfile.id)
        .where(anchor <= _cutoff_by_tier(now))
    )
    return list(result.scalars().all())


_RETAIN_COUNT_BY_TIER = case(
    *[
        (MemoryBackup.subscription_tier == tier, policy.retain_count)
        for tier, policy in BACKUP_POLICIES.items()
    ]
)


async def _prune_stale_backups(session: AsyncSession, clone: CloneProfile) -> int:
    ranked = (
        select(
            MemoryBackup.id,
            MemoryBackup.storage_key,
            func.row_number()
            .over(order_by=MemoryBackup.period_end.desc())
            .label("rank"),
            _RETAIN_COUNT_BY_TIER.label("retain_count"),
        )
        .where(MemoryBackup.clone_id == clone.id)
        .subquery()
    )

    result = await session.execute(
        select(ranked.c.id, ranked.c.storage_key).where(
            ranked.c.rank > ranked.c.retain_count
        )
    )
    stale = result.all()
    if not stale:
        return 0

    for _, storage_key in stale:
        await object_storage.delete_object(storage_key)

    await session.execute(
        delete(MemoryBackup)
        .where(MemoryBackup.id.in_([backup_id for backup_id, _ in stale]))
        .execution_options(synchronize_session=False)
    )
    return len(stale)


async def rollup_clone(
    session: AsyncSession, clone: CloneProfile, now: datetime
) -> dict[str, str | int]:
    buffer = await session.get(MemoryBuffer, clone.id)
    if buffer is None or buffer.entry_count == 0:
        return {"clone_id": clone.id, "status": "empty", "entry_count": 0}

    payload = buffer.payload
    period_start = min(
        _aware(datetime.fromisoformat(item["captured_at"])) for item in payload
    )

    storage_key = object_storage.backup_key(clone.id, period_start, now)
    await object_storage.put_object(storage_key, buffer.payload_blob)

    backup = MemoryBackup(
        clone_id=clone.id,
        period_start=period_start,
        period_end=now,
        entry_count=buffer.entry_count,
        storage_key=storage_key,
        subscription_tier=clone.subscription_tier,
    )
    session.add(backup)
    await session.delete(buffer)
    await session.commit()

    pruned = await _prune_stale_backups(session, clone)
    await session.commit()

    logger.info(
        "rolled up clone_id=%d entries=%d pruned=%d",
        clone.id,
        backup.entry_count,
        pruned,
    )
    return {
        "clone_id": clone.id,
        "status": "backed_up",
        "entry_count": backup.entry_count,
    }


async def run_rollup(session: AsyncSession) -> list[dict[str, str | int]]:
    now = datetime.now(UTC)
    due = await find_due_clones(session, now)

    results = []
    for clone in due:
        try:
            results.append(await rollup_clone(session, clone, now))
        except Exception:
            logger.exception("rollup failed for clone_id=%d, skipping", clone.id)
            await session.rollback()
    return results
