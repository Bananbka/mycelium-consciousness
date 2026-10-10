"""Lab 8: /fleet endpoints on top of the guarded cache."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from api.routers import fleet
from shared import bloom, guarded_cache
from shared.db.models import MemoryBackup
from shared.subscriptions import SubscriptionTier


class FakeAccess:
    """Hands the test transaction to compute functions (they normally open
    their own sessions, which cannot see uncommitted test rows)."""

    def __init__(self, session):
        self.session = session

    @asynccontextmanager
    async def control(self):
        yield self.session

    async def fan_out(self, fn):
        return {"test": await fn("test", self.session)}

    def shard_session(self, _clone_id):
        return self.control()


@pytest.fixture(autouse=True)
def _access(session, monkeypatch):
    monkeypatch.setattr(fleet, "data_access", FakeAccess(session))


async def _backup(session, clone_id: int, entries: int) -> None:
    now = datetime.now(UTC)
    session.add(
        MemoryBackup(
            clone_id=clone_id,
            period_start=now,
            period_end=now,
            entry_count=entries,
            storage_key=f"t/{clone_id}/{entries}",
            subscription_tier=SubscriptionTier.FREE,
        )
    )
    await session.flush()


async def test_leaderboard_ranks_clones_and_is_cached(
    client, session, clone_headers, clone_user, other_clone_user
):
    await _backup(session, clone_user.profile.id, 5)
    await _backup(session, clone_user.profile.id, 7)
    await _backup(session, other_clone_user.profile.id, 20)

    first = await client.get("/fleet/leaderboard", headers=clone_headers)
    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    assert [(e["designation"], e["entries"]) for e in first.json()] == [
        ("clone-beta", 20),
        ("clone-alpha", 12),
    ]

    second = await client.get("/fleet/leaderboard", headers=clone_headers)
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == first.json()

    counters = await guarded_cache.metrics("leaderboard")
    assert counters["computes"] == 1


async def test_leaderboard_requires_authentication(client):
    assert (await client.get("/fleet/leaderboard")).status_code == 401


async def test_admin_flush_forces_a_recompute(client, clone_headers, admin_headers):
    await client.get("/fleet/leaderboard", headers=clone_headers)
    flushed = await client.post("/admin/cache/flush", headers=admin_headers)
    assert flushed.status_code == 200 and flushed.json()["removed"] >= 1
    after = await client.get("/fleet/leaderboard", headers=clone_headers)
    assert after.headers["x-cache"] == "MISS"


async def test_cache_flush_is_admin_only(client, clone_headers):
    assert (
        await client.post("/admin/cache/flush", headers=clone_headers)
    ).status_code == 403


async def test_protection_override_is_ignored_unless_enabled(
    client, clone_headers, monkeypatch
):
    response = await client.get(
        "/fleet/leaderboard", headers={**clone_headers, "X-Cache-Protection": "none"}
    )
    assert response.headers["x-cache-protection"] == guarded_cache.DEFAULT_PROTECTION

    monkeypatch.setattr(fleet, "ALLOW_OVERRIDE", True)
    allowed = await client.get(
        "/fleet/leaderboard?limit=3",
        headers={**clone_headers, "X-Cache-Protection": "none"},
    )
    assert allowed.headers["x-cache-protection"] == "none"


async def test_fleet_clone_found_and_cached(
    client, session, clone_headers, other_clone_user
):
    other = other_clone_user.profile
    await _backup(session, other.id, 3)
    first = await client.get(f"/fleet/clones/{other.id}", headers=clone_headers)
    assert first.status_code == 200
    assert first.json() == {
        "id": other.id,
        "designation": "clone-beta",
        "status": "active",
        "backups": 1,
    }
    assert first.headers["x-cache"] == "MISS"
    again = await client.get(f"/fleet/clones/{other.id}", headers=clone_headers)
    assert again.headers["x-cache"] == "HIT"


async def test_missing_clone_is_negatively_cached(client, clone_headers):
    first = await client.get("/fleet/clones/9999999", headers=clone_headers)
    assert first.status_code == 404
    assert first.headers["x-cache"] == "MISS"
    second = await client.get("/fleet/clones/9999999", headers=clone_headers)
    assert second.status_code == 404
    assert second.headers["x-cache"] == "NEGATIVE_HIT"
    assert (await guarded_cache.metrics("fleet-clone"))["computes"] == 1


async def test_bloom_filter_rejects_without_touching_cache_or_database(
    client, clone_headers, clone_user
):
    await bloom.load([clone_user.profile.id])
    response = await client.get("/fleet/clones/9999999", headers=clone_headers)
    assert response.status_code == 404
    assert response.headers["x-cache"] == "BLOOM_REJECT"
    counters = await guarded_cache.metrics("fleet-clone")
    assert counters.get("computes", 0) == 0
    assert counters["bloom_rejects"] == 1

    # an id that does exist still gets through
    found = await client.get(
        f"/fleet/clones/{clone_user.profile.id}", headers=clone_headers
    )
    assert found.status_code == 200


async def test_register_adds_the_new_clone_to_the_filter(client):
    await bloom.load([])  # loaded and empty: everything not added is rejected
    email = "fresh@clones.example.com"
    secret = uuid.uuid4().hex
    created = await client.post(
        "/auth/register",
        json={"email": email, "password": secret, "designation": "clone-fresh"},
    )
    assert created.status_code == 201
    login = await client.post("/auth/login", json={"email": email, "password": secret})
    token = login.json()["access_token"]
    me = (
        await client.get("/profiles/me", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert await bloom.might_contain(me["id"])
