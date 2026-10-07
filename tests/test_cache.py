"""Redis read-through caches: hits, invalidation on writes, graceful degradation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from api.security import hash_password_async, verify_password_async
from celery_worker.pipeline import rollup_clone
from shared import backup_codec, cache, streams
from shared.db.models import MemoryBackup, MemoryBuffer
from shared.settings import settings
from shared.subscriptions import SubscriptionTier


@pytest.fixture
def caches_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_cache_ttl_seconds", 30)
    monkeypatch.setattr(settings, "backups_cache_ttl_seconds", 30)


async def _exists(key: str) -> bool:
    return bool(await streams.get_redis().exists(key))


async def test_principal_is_cached_after_first_request(
    client, clone_user, clone_headers, caches_on
):
    key = cache.principal_key(clone_user.id)
    assert not await _exists(key)

    first = await client.get("/profiles/me", headers=clone_headers)
    assert await _exists(key)
    second = await client.get("/profiles/me", headers=clone_headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert await streams.get_redis().ttl(key) <= 30


async def test_cached_principal_never_contains_the_password_hash(
    clone_user, clone_headers, client, caches_on
):
    await client.get("/profiles/me", headers=clone_headers)
    raw = await streams.get_redis().get(cache.principal_key(clone_user.id))
    assert clone_user.password_hash not in raw
    assert "password" not in raw


async def test_patching_the_profile_is_visible_immediately(
    client, clone_headers, caches_on
):
    before = await client.get("/profiles/me", headers=clone_headers)
    assert before.json()["status"] == "active"

    patched = await client.patch(
        "/profiles/me", headers=clone_headers, json={"status": "deceased"}
    )
    assert patched.status_code == 200

    after = await client.get("/profiles/me", headers=clone_headers)
    assert after.json()["status"] == "deceased"


async def test_admin_tier_change_invalidates_the_cached_profile(
    client, clone_user, clone_headers, admin_headers, caches_on
):
    await client.get("/profiles/me", headers=clone_headers)
    changed = await client.patch(
        f"/admin/clones/{clone_user.profile.id}/subscription",
        headers=admin_headers,
        json={"subscription_tier": "premium"},
    )
    assert changed.status_code == 200

    after = await client.get("/profiles/me", headers=clone_headers)
    assert after.json()["subscription_tier"] == "premium"


async def test_deactivation_takes_effect_despite_the_cache(
    client, clone_user, clone_headers, admin_headers, caches_on
):
    assert (await client.get("/profiles/me", headers=clone_headers)).status_code == 200
    response = await client.post(
        f"/admin/users/{clone_user.id}/deactivate", headers=admin_headers
    )
    assert response.status_code == 200

    assert (await client.get("/profiles/me", headers=clone_headers)).status_code == 401


async def test_cache_disabled_by_zero_ttl(
    client, clone_user, clone_headers, monkeypatch
):
    monkeypatch.setattr(settings, "auth_cache_ttl_seconds", 0)
    await client.get("/profiles/me", headers=clone_headers)
    assert not await _exists(cache.principal_key(clone_user.id))


async def test_redis_outage_degrades_to_a_cache_miss(
    client, clone_headers, caches_on, monkeypatch
):
    async def boom(*_a, **_kw):
        raise RedisConnectionError("down")

    monkeypatch.setattr(
        cache,
        "get_redis",
        lambda: type("R", (), {"get": boom, "set": boom, "delete": boom})(),
    )
    response = await client.get("/profiles/me", headers=clone_headers)
    assert response.status_code == 200


async def _backup(session, clone_id: int, tier=SubscriptionTier.FREE) -> MemoryBackup:
    now = datetime.now(UTC)
    backup = MemoryBackup(
        clone_id=clone_id,
        period_start=now,
        period_end=now,
        entry_count=1,
        storage_key=f"test/{clone_id}/{now.timestamp()}",
        subscription_tier=tier,
    )
    session.add(backup)
    await session.flush()
    return backup


async def test_backup_listing_is_cached_and_scoped_to_the_owner(
    client, session, clone_user, other_clone_user, clone_headers, caches_on
):
    mine = await _backup(session, clone_user.profile.id)
    await _backup(session, other_clone_user.profile.id)

    first = await client.get("/memories/backups", headers=clone_headers)
    assert await _exists(cache.backups_key(clone_user.profile.id))
    second = await client.get("/memories/backups", headers=clone_headers)

    assert first.json() == second.json()
    assert [b["id"] for b in second.json()] == [mine.id]


async def test_limit_variants_are_cached_separately(
    client, session, clone_user, clone_headers, caches_on
):
    for _ in range(3):
        await _backup(session, clone_user.profile.id)

    one = await client.get("/memories/backups?limit=1", headers=clone_headers)
    three = await client.get("/memories/backups?limit=3", headers=clone_headers)
    assert len(one.json()) == 1
    assert len(three.json()) == 3


async def test_rollup_invalidates_the_backup_listing(
    client, session, clone_user, clone_headers, caches_on
):
    profile = clone_user.profile
    assert (await client.get("/memories/backups", headers=clone_headers)).json() == []
    assert await _exists(cache.backups_key(profile.id))

    now = datetime.now(UTC)
    session.add(
        MemoryBuffer(
            clone_id=profile.id,
            payload_blob=backup_codec.encode_payload(
                [{"content": "x", "captured_at": now.isoformat()}]
            ),
            entry_count=1,
        )
    )
    await session.flush()
    await rollup_clone(session, profile, now)

    assert not await _exists(cache.backups_key(profile.id))
    listing = await client.get("/memories/backups", headers=clone_headers)
    assert len(listing.json()) == 1


async def test_restore_invalidates_the_backup_listing(
    client, session, clone_user, clone_headers, caches_on
):
    now = datetime.now(UTC)
    from shared import object_storage

    key = object_storage.backup_key(clone_user.profile.id, now, now)
    await object_storage.put_object(key, backup_codec.encode_payload([]))
    backup = MemoryBackup(
        clone_id=clone_user.profile.id,
        period_start=now,
        period_end=now,
        entry_count=0,
        storage_key=key,
        subscription_tier=SubscriptionTier.FREE,
    )
    session.add(backup)
    await session.flush()

    listed = await client.get("/memories/backups", headers=clone_headers)
    assert listed.json()[0]["restored_at"] is None

    restored = await client.post(
        f"/memories/backups/{backup.id}/restore", headers=clone_headers
    )
    assert restored.status_code == 200
    listed = await client.get("/memories/backups", headers=clone_headers)
    assert listed.json()[0]["restored_at"] is not None


async def test_append_memory_pipeline_returns_entry_id_and_length():
    entry_id, length = await streams.append_memory(7, "a", "2026-01-01T00:00:00+00:00")
    _, length2 = await streams.append_memory(7, "b", "2026-01-01T00:00:01+00:00")
    assert "-" in entry_id
    assert (length, length2) == (1, 2)


async def test_async_password_helpers_round_trip():
    hashed = await hash_password_async("correct horse battery")
    assert await verify_password_async("correct horse battery", hashed)
    assert not await verify_password_async("wrong", hashed)
