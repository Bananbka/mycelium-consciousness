"""The periodic backup rollup: cadence, payload contents, retention pruning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from celery_worker.pipeline import find_due_clones, rollup_clone, run_rollup
from shared import backup_codec, object_storage
from shared.db.models import MemoryBackup, MemoryBuffer
from shared.subscriptions import SubscriptionTier


async def _write(
    session, profile_id: int, content: str, when: datetime | None = None
) -> None:
    when = when or datetime.now(UTC)
    buffer = await session.get(MemoryBuffer, profile_id)
    existing = buffer.payload if buffer is not None else []
    if buffer is None:
        buffer = MemoryBuffer(clone_id=profile_id, payload_blob=b"", entry_count=0)
        session.add(buffer)

    combined = [*existing, {"content": content, "captured_at": when.isoformat()}]
    buffer.payload_blob = backup_codec.encode_payload(combined)
    buffer.entry_count = len(combined)
    await session.flush()


async def _entry_count(session, profile_id: int) -> int:
    buffer = await session.get(MemoryBuffer, profile_id)
    return buffer.entry_count if buffer is not None else 0


async def _payload_of(backup: MemoryBackup) -> list[dict]:
    raw = await object_storage.get_object(backup.storage_key)
    return backup_codec.decode_payload(raw)


async def test_a_brand_new_clone_is_not_due_immediately(session, clone_user):
    now = datetime.now(UTC)
    due = await find_due_clones(session, now)
    assert clone_user.profile.id not in {c.id for c in due}


async def test_a_premium_clone_is_due_after_one_day(session, clone_user):
    clone_user.profile.subscription_tier = SubscriptionTier.PREMIUM
    clone_user.profile.created_at = datetime.now(UTC) - timedelta(days=2)
    await session.flush()

    due = await find_due_clones(session, datetime.now(UTC))
    assert clone_user.profile.id in {c.id for c in due}


async def test_a_free_clone_is_not_due_after_one_week(session, clone_user):
    clone_user.profile.subscription_tier = SubscriptionTier.FREE
    clone_user.profile.created_at = datetime.now(UTC) - timedelta(days=7)
    await session.flush()

    due = await find_due_clones(session, datetime.now(UTC))
    assert clone_user.profile.id not in {c.id for c in due}


async def test_rollup_folds_every_buffered_frame_into_one_backup(session, clone_user):
    profile = clone_user.profile
    for i in range(5):
        await _write(session, profile.id, f"noise {i}")

    result = await rollup_clone(session, profile, datetime.now(UTC))
    assert result["status"] == "backed_up"
    assert result["entry_count"] == 5

    backup = await session.scalar(
        select(MemoryBackup).where(MemoryBackup.clone_id == profile.id)
    )
    assert backup is not None
    assert backup.entry_count == 5
    payload = await _payload_of(backup)
    assert {item["content"] for item in payload} == {f"noise {i}" for i in range(5)}


async def test_rollup_deletes_entries_after_backing_up(session, clone_user):
    profile = clone_user.profile
    await _write(session, profile.id, "will be rolled up")

    await rollup_clone(session, profile, datetime.now(UTC))

    assert await _entry_count(session, profile.id) == 0


async def test_rollup_of_no_entries_creates_no_backup(session, clone_user):
    profile = clone_user.profile
    result = await rollup_clone(session, profile, datetime.now(UTC))

    assert result["status"] == "empty"
    assert result["entry_count"] == 0

    count = await session.scalar(
        select(MemoryBackup).where(MemoryBackup.clone_id == profile.id)
    )
    assert count is None


async def test_a_second_write_after_rollup_is_not_lost(session, clone_user):
    profile = clone_user.profile
    await _write(session, profile.id, "first period")

    await rollup_clone(session, profile, datetime.now(UTC))
    await _write(session, profile.id, "second period")

    assert await _entry_count(session, profile.id) == 1


async def test_retention_keeps_only_the_tiers_allotted_backups(session, clone_user):
    profile = clone_user.profile
    profile.subscription_tier = SubscriptionTier.FREE  # retains 1
    await session.flush()

    now = datetime.now(UTC)
    for day in range(3):
        await _write(session, profile.id, f"period {day}")
        await rollup_clone(session, profile, now + timedelta(days=day))

    remaining = (
        await session.scalars(
            select(MemoryBackup).where(MemoryBackup.clone_id == profile.id)
        )
    ).all()
    assert len(remaining) == 1
    payload = await _payload_of(remaining[0])
    assert payload[0]["content"] == "period 2"


async def test_tier_downgrade_does_not_delete_backups_already_taken(
    session, clone_user
):
    profile = clone_user.profile
    profile.subscription_tier = SubscriptionTier.PREMIUM  # retains 30
    await session.flush()

    now = datetime.now(UTC)
    for day in range(3):
        await _write(session, profile.id, f"premium period {day}")
        await rollup_clone(session, profile, now + timedelta(days=day))

    profile.subscription_tier = SubscriptionTier.FREE  # retains 1
    await session.flush()

    await _write(session, profile.id, "free period")
    await rollup_clone(session, profile, now + timedelta(days=3))

    remaining = (
        await session.scalars(
            select(MemoryBackup).where(MemoryBackup.clone_id == profile.id)
        )
    ).all()
    contents = set()
    for backup in remaining:
        payload = await _payload_of(backup)
        contents.update(item["content"] for item in payload)

    assert len(remaining) == 4
    assert contents == {
        "premium period 0",
        "premium period 1",
        "premium period 2",
        "free period",
    }


async def test_run_rollup_only_processes_due_clones(
    session, clone_user, other_clone_user
):
    clone_user.profile.subscription_tier = SubscriptionTier.PREMIUM
    clone_user.profile.created_at = datetime.now(UTC) - timedelta(days=2)
    other_clone_user.profile.subscription_tier = SubscriptionTier.FREE
    await session.flush()

    await _write(session, clone_user.profile.id, "due clone noise")
    await _write(session, other_clone_user.profile.id, "not due clone noise")

    results = await run_rollup(session)

    processed_ids = {r["clone_id"] for r in results}
    assert clone_user.profile.id in processed_ids
    assert other_clone_user.profile.id not in processed_ids
    assert await _entry_count(session, other_clone_user.profile.id) == 1
