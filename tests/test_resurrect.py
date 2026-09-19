"""Resurrecting a new clone from a dead clone's last backup."""

from __future__ import annotations

from datetime import UTC, datetime

from shared import backup_codec, object_storage
from shared.db.models import MemoryBackup
from shared.subscriptions import SubscriptionTier


async def _make_backup(clone_id: int, content: str) -> MemoryBackup:
    now = datetime.now(UTC)
    storage_key = object_storage.backup_key(clone_id, now, now)
    await object_storage.put_object(
        storage_key,
        backup_codec.encode_payload(
            [{"content": content, "captured_at": now.isoformat()}]
        ),
    )
    return MemoryBackup(
        clone_id=clone_id,
        period_start=now,
        period_end=now,
        entry_count=1,
        storage_key=storage_key,
        subscription_tier=SubscriptionTier.FREE,
    )


async def test_resurrect_inherits_the_dead_clones_latest_backup(
    client, session, clone_headers, other_clone_user
):
    other_clone_user.profile.status = "deceased"
    session.add(await _make_backup(other_clone_user.profile.id, "beta's last memory"))
    await session.flush()

    response = await client.post(
        "/memories/resurrect",
        headers=clone_headers,
        json={"source_profile_id": other_clone_user.profile.id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["payload"] == [
        {
            "content": "beta's last memory",
            "captured_at": body["payload"][0]["captured_at"],
        }
    ]
    assert body["restored_at"] is not None


async def test_cannot_resurrect_from_a_still_active_clone(
    client, session, clone_headers, other_clone_user
):
    session.add(await _make_backup(other_clone_user.profile.id, "beta is still alive"))
    await session.flush()

    response = await client.post(
        "/memories/resurrect",
        headers=clone_headers,
        json={"source_profile_id": other_clone_user.profile.id},
    )

    assert response.status_code == 409


async def test_cannot_resurrect_from_a_clone_with_no_backups(
    client, session, clone_headers, other_clone_user
):
    other_clone_user.profile.status = "deceased"
    await session.flush()

    response = await client.post(
        "/memories/resurrect",
        headers=clone_headers,
        json={"source_profile_id": other_clone_user.profile.id},
    )

    assert response.status_code == 404


async def test_cannot_resurrect_from_a_nonexistent_clone(client, clone_headers):
    response = await client.post(
        "/memories/resurrect",
        headers=clone_headers,
        json={"source_profile_id": 999999},
    )

    assert response.status_code == 404


async def test_cannot_resurrect_from_own_profile(
    client, session, clone_headers, clone_user
):
    clone_user.profile.status = "deceased"
    session.add(await _make_backup(clone_user.profile.id, "alpha's own memory"))
    await session.flush()

    response = await client.post(
        "/memories/resurrect",
        headers=clone_headers,
        json={"source_profile_id": clone_user.profile.id},
    )

    assert response.status_code == 400
