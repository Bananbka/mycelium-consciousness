"""Lab 6: read/write splitting, read-after-write consistency, node failover.

There is no second Postgres in the test environment, so the standby is a thin
proxy over the test session: it records how often it is used and can be made
to fail like an unreachable node. What is under test is the routing policy,
not PostgreSQL's replication.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api import deps, main
from api.main import app
from celery_worker.pipeline import rollup_clone
from shared import backup_codec, cache, consistency
from shared.db.db import get_db
from shared.db.models import MemoryBuffer


class FakeReplica:
    """Stands in for ``ReplicaSessionLocal``: callable, async context manager."""

    def __init__(self, session):
        self.session = session
        self.checkouts = 0
        self.down = False

    def __call__(self):
        return _Handle(self)


class _Handle:
    def __init__(self, owner: FakeReplica):
        self._owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def connection(self):
        self._owner.checkouts += 1
        if self._owner.down:
            raise ConnectionRefusedError("replica is down")

    async def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._owner.session, name)


@pytest.fixture
def replica(monkeypatch, session):
    fake = FakeReplica(session)
    monkeypatch.setattr(deps, "REPLICA_ENABLED", True)
    monkeypatch.setattr(deps, "ReplicaSessionLocal", fake)
    monkeypatch.setattr(main, "REPLICA_ENABLED", True)
    consistency.replica_breaker.reset()
    yield fake
    consistency.replica_breaker.reset()


def node(response) -> str:
    return response.headers["x-db-node"]


async def _expire_window() -> None:
    client = cache._get_client()
    async for key in client.scan_iter(match="rw:*"):
        await client.delete(key)


async def test_without_a_replica_every_read_uses_the_primary(client, clone_headers):
    response = await client.get("/profiles", headers=clone_headers)
    assert response.status_code == 200
    assert node(response) == "primary"


async def test_read_goes_to_replica_when_user_has_not_written(
    client, clone_headers, replica
):
    response = await client.get("/profiles", headers=clone_headers)
    assert response.status_code == 200
    assert node(response) == "replica"
    assert replica.checkouts == 1


async def test_write_pins_the_writers_next_reads_to_the_primary(
    client, clone_headers, replica
):
    patch = await client.patch(
        "/profiles/me", json={"status": "dormant"}, headers=clone_headers
    )
    assert patch.status_code == 200
    assert node(patch) == "primary"

    read = await client.get("/profiles", headers=clone_headers)
    assert node(read) == "primary-sticky"
    assert read.json()[0]["status"] == "dormant"
    assert replica.checkouts == 0


async def test_window_expiry_returns_reads_to_the_replica(
    client, clone_headers, replica
):
    await client.patch("/profiles/me", json={"status": "x"}, headers=clone_headers)
    await _expire_window()
    read = await client.get("/profiles", headers=clone_headers)
    assert node(read) == "replica"


async def test_rejected_write_does_not_open_the_window(
    client, clone_headers, other_clone_user, replica
):
    # 403: another clone's profile -> nothing was written.
    patch = await client.patch(
        f"/profiles/{other_clone_user.profile.id}",
        json={"status": "x"},
        headers=clone_headers,
    )
    assert patch.status_code == 403
    read = await client.get("/profiles", headers=clone_headers)
    assert node(read) == "replica"


async def test_windows_are_per_user(
    client, clone_headers, other_clone_headers, replica
):
    await client.patch("/profiles/me", json={"status": "x"}, headers=clone_headers)
    other = await client.get("/profiles", headers=other_clone_headers)
    assert node(other) == "replica"


async def test_strong_header_forces_primary_and_eventual_forces_replica(
    client, clone_headers, replica
):
    await client.patch("/profiles/me", json={"status": "x"}, headers=clone_headers)
    eventual = await client.get(
        "/profiles", headers={**clone_headers, "X-Read-Consistency": "eventual"}
    )
    assert node(eventual) == "replica"

    await _expire_window()
    strong = await client.get(
        "/profiles", headers={**clone_headers, "X-Read-Consistency": "strong"}
    )
    assert node(strong) == "primary-strong"


async def test_identity_and_profile_me_are_always_primary(
    client, clone_headers, replica
):
    response = await client.get("/profiles/me", headers=clone_headers)
    assert node(response) == "primary"
    assert replica.checkouts == 0


async def test_unreachable_replica_falls_back_and_trips_the_breaker(
    client, clone_headers, replica
):
    replica.down = True
    first = await client.get("/profiles", headers=clone_headers)
    assert first.status_code == 200
    assert node(first) == "primary-fallback"
    assert replica.checkouts == 1

    # Breaker open: the next request does not even try the standby.
    second = await client.get("/profiles", headers=clone_headers)
    assert node(second) == "primary-fallback"
    assert replica.checkouts == 1

    # Recovered and cooldown elapsed.
    replica.down = False
    consistency.replica_breaker.reset()
    third = await client.get("/profiles", headers=clone_headers)
    assert node(third) == "replica"


async def test_rollup_opens_the_window_for_the_owner(
    client, session, clone_headers, clone_user, replica
):
    """A rollup is a write the clone did not make; its next listing must not be
    cached from a standby that has not replayed the new backup row yet."""
    now = datetime.now(UTC)
    session.add(
        MemoryBuffer(
            clone_id=clone_user.profile.id,
            payload_blob=backup_codec.encode_payload(
                [{"content": "x", "captured_at": now.isoformat()}]
            ),
            entry_count=1,
        )
    )
    await session.flush()
    await rollup_clone(session, clone_user.profile, now)

    listing = await client.get("/memories/backups", headers=clone_headers)
    assert node(listing) == "primary-sticky"
    assert len(listing.json()) == 1


class _DeadPrimary:
    """A primary whose every statement fails like a refused connection."""

    async def execute(self, *args, **kwargs):
        raise ConnectionRefusedError("primary is down")

    scalar = commit = rollback = refresh = get = execute


@pytest.fixture
def primary_down(client, clone_headers, replica):
    async def dead():
        yield _DeadPrimary()

    # `client` clears overrides on teardown.
    app.dependency_overrides[get_db] = dead


async def test_primary_down_serves_safe_reads_from_the_replica(
    client, clone_headers, primary_down
):
    response = await client.get("/profiles", headers=clone_headers)
    assert response.status_code == 200
    assert node(response) == "replica-degraded"
    assert response.json()[0]["designation"] == "clone-alpha"


async def test_primary_down_refuses_writes_with_503(
    client, clone_headers, primary_down
):
    response = await client.patch(
        "/profiles/me", json={"status": "x"}, headers=clone_headers
    )
    assert response.status_code == 503
    assert "read-only" in response.json()["detail"]
    assert response.headers["retry-after"] == "5"


async def test_primary_down_without_credentials_is_503_not_500(client, primary_down):
    response = await client.post(
        "/auth/login", json={"email": "a@b.co", "password": "whatever-123"}
    )
    assert response.status_code == 503
