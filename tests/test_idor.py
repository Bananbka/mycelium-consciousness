"""Horizontal access control: one clone must not reach another clone's data."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from shared import backup_codec, object_storage, streams
from shared.db.models import CloneProfile, MemoryBackup
from shared.subscriptions import SubscriptionTier


async def _profile_id(session, designation: str) -> int:
    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.designation == designation)
    )
    assert profile is not None
    return profile.id


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


async def test_clone_cannot_read_another_clones_profile(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    response = await client.get(f"/profiles/{victim_id}", headers=clone_headers)
    assert response.status_code == 403


async def test_clone_cannot_patch_another_clones_profile(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    response = await client.patch(
        f"/profiles/{victim_id}",
        headers=clone_headers,
        json={"designation": "pwned", "status": "compromised"},
    )
    assert response.status_code == 403


async def test_a_rejected_idor_patch_does_not_mutate_the_victim(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    await client.patch(
        f"/profiles/{victim_id}",
        headers=clone_headers,
        json={"designation": "pwned", "status": "compromised"},
    )

    victim = await session.get(CloneProfile, victim_id)
    await session.refresh(victim)
    assert victim.designation == "clone-beta"
    assert victim.status == "active"


async def test_clone_can_patch_its_own_profile(client, session, clone_headers):
    own_id = await _profile_id(session, "clone-alpha")

    response = await client.patch(
        f"/profiles/{own_id}",
        headers=clone_headers,
        json={"status": "dormant"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dormant"


async def test_admin_may_patch_any_profile(client, session, admin_headers, clone_user):
    target_id = await _profile_id(session, "clone-alpha")

    response = await client.patch(
        f"/profiles/{target_id}",
        headers=admin_headers,
        json={"status": "quarantined"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"


async def test_profile_listing_is_scoped_to_the_caller(
    client, clone_headers, other_clone_user
):
    response = await client.get("/profiles", headers=clone_headers)
    assert response.status_code == 200

    designations = {item["designation"] for item in response.json()}
    assert designations == {"clone-alpha"}


async def test_admin_listing_sees_every_profile(
    client, admin_headers, clone_user, other_clone_user
):
    response = await client.get("/profiles", headers=admin_headers)
    assert response.status_code == 200

    designations = {item["designation"] for item in response.json()}
    assert {"clone-alpha", "clone-beta"} <= designations


async def test_write_never_reaches_another_clones_stream(
    client, clone_headers, clone_user, other_clone_user
):
    await client.post(
        "/memories/write", headers=clone_headers, json={"content": "alpha only"}
    )

    other_entries = await streams.read_all(other_clone_user.profile.id)
    assert other_entries == []


async def test_clone_cannot_read_another_clones_backup(
    client, session, clone_headers, other_clone_user
):
    backup = await _make_backup(other_clone_user.profile.id, "beta secret")
    session.add(backup)
    await session.flush()

    response = await client.get(f"/memories/backups/{backup.id}", headers=clone_headers)
    assert response.status_code == 404


async def test_clone_cannot_restore_another_clones_backup(
    client, session, clone_headers, other_clone_user
):
    backup = await _make_backup(other_clone_user.profile.id, "beta secret")
    session.add(backup)
    await session.flush()

    response = await client.post(
        f"/memories/backups/{backup.id}/restore", headers=clone_headers
    )
    assert response.status_code == 404

    await session.refresh(backup)
    assert backup.restored_at is None


async def test_backup_listing_never_includes_another_clones_rows(
    client, session, clone_headers, other_clone_user
):
    backup = await _make_backup(other_clone_user.profile.id, "beta secret")
    session.add(backup)
    await session.flush()

    response = await client.get("/memories/backups", headers=clone_headers)
    assert response.status_code == 200
    assert response.json() == []
