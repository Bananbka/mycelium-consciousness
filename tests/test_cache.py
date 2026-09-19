"""Lab 4: cache-aside on GET /memories/backups (hit/miss, invalidation, fallback)."""

from __future__ import annotations

from datetime import UTC, datetime

from celery_worker.pipeline import rollup_clone
from shared import backup_codec, cache, object_storage
from shared.db.models import MemoryBackup, MemoryBuffer
from shared.subscriptions import SubscriptionTier


async def _add_backup(session, clone_id: int) -> MemoryBackup:
    now = datetime.now(UTC)
    key = object_storage.backup_key(clone_id, now, now)
    await object_storage.put_object(
        key,
        backup_codec.encode_payload([{"content": "x", "captured_at": now.isoformat()}]),
    )
    backup = MemoryBackup(
        clone_id=clone_id,
        period_start=now,
        period_end=now,
        entry_count=1,
        storage_key=key,
        subscription_tier=SubscriptionTier.FREE,
    )
    session.add(backup)
    await session.flush()
    return backup


async def test_first_read_misses_second_read_hits(client, clone_headers):
    first = await client.get("/memories/backups", headers=clone_headers)
    second = await client.get("/memories/backups", headers=clone_headers)
    assert first.headers["x-cache"] == "MISS"
    assert second.headers["x-cache"] == "HIT"
    assert first.json() == second.json()


async def test_different_params_use_different_keys(client, clone_headers):
    await client.get("/memories/backups?limit=5", headers=clone_headers)
    other = await client.get("/memories/backups?limit=6", headers=clone_headers)
    assert other.headers["x-cache"] == "MISS"


async def test_cache_is_per_clone(client, session, clone_headers, other_clone_headers):
    await client.get("/memories/backups", headers=clone_headers)
    theirs = await client.get("/memories/backups", headers=other_clone_headers)
    assert theirs.headers["x-cache"] == "MISS"


async def test_no_cache_header_bypasses_and_does_not_populate(client, clone_headers):
    bypass = await client.get(
        "/memories/backups", headers={**clone_headers, "Cache-Control": "no-cache"}
    )
    assert bypass.headers["x-cache"] == "BYPASS"
    after = await client.get("/memories/backups", headers=clone_headers)
    assert after.headers["x-cache"] == "MISS"


async def test_restore_invalidates_so_next_read_is_fresh(
    client, session, clone_headers, clone_user
):
    backup = await _add_backup(session, clone_user.profile.id)
    warm = await client.get("/memories/backups", headers=clone_headers)
    assert warm.json()[0]["restored_at"] is None
    hit = await client.get("/memories/backups", headers=clone_headers)
    assert hit.headers["x-cache"] == "HIT"

    restore = await client.post(
        f"/memories/backups/{backup.id}/restore", headers=clone_headers
    )
    assert restore.status_code == 200

    fresh = await client.get("/memories/backups", headers=clone_headers)
    assert fresh.headers["x-cache"] == "MISS"
    assert fresh.json()[0]["restored_at"] is not None


async def test_rollup_invalidates_the_listing(
    client, session, clone_headers, clone_user
):
    profile = clone_user.profile
    warm = await client.get("/memories/backups", headers=clone_headers)
    assert warm.json() == []

    now = datetime.now(UTC)
    session.add(
        MemoryBuffer(
            clone_id=profile.id,
            payload_blob=backup_codec.encode_payload(
                [{"content": "a", "captured_at": now.isoformat()}]
            ),
            entry_count=1,
        )
    )
    await session.flush()
    await rollup_clone(session, profile, now)

    fresh = await client.get("/memories/backups", headers=clone_headers)
    assert fresh.headers["x-cache"] == "MISS"
    assert len(fresh.json()) == 1


async def test_dead_cache_falls_back_to_the_database(
    client, session, clone_headers, clone_user, monkeypatch
):
    await _add_backup(session, clone_user.profile.id)

    def _broken():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "_get_client", _broken)
    response = await client.get("/memories/backups", headers=clone_headers)
    assert response.status_code == 200
    assert response.headers["x-cache"] == "BYPASS"
    assert len(response.json()) == 1
