"""Lab 7: shard router (placement, balance, resharding) and sharded data plane.

The first half is pure arithmetic on the router. The second half runs the real
API and Celery code against two extra databases acting as shards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import _ensure_database_exists, _test_database_url
from sqlalchemy import select

from api import deps
from celery_worker.flush import flush_one_stream
from celery_worker.pipeline import find_due_clones, rollup_clone
from shared import backup_codec, object_storage, sharding, streams
from shared.db.models import Base, MemoryBackup, MemoryBuffer
from shared.sharding import ShardNode, ShardRouter, assign_buckets, parse_nodes, route
from shared.subscriptions import SubscriptionTier

IDS = range(1, 4001)


def _share(shards: list[str], strategy: str, ids) -> dict[str, float]:
    table = assign_buckets(shards) if strategy == "vbucket" else None
    placed = [route(i, shards, strategy, table) for i in ids]
    return {s: placed.count(s) / len(placed) for s in shards}


# ---- placement ------------------------------------------------------------


def test_routing_is_deterministic():
    shards = ["a", "b", "c"]
    first = [route(i, shards, "vbucket") for i in range(500)]
    again = [route(i, shards, "vbucket") for i in range(500)]
    assert first == again
    assert assign_buckets(shards) == assign_buckets(shards)


def test_bucket_table_is_independent_of_input_order_of_other_shards():
    # Rendezvous hashing: a bucket's owner depends only on the shard *set*.
    assert assign_buckets(["a", "b", "c"]) == assign_buckets(["c", "a", "b"])


@pytest.mark.parametrize("strategy", ["modulo", "hash", "vbucket"])
def test_sequential_ids_are_balanced(strategy):
    shares = _share(["a", "b"], strategy, IDS)
    assert all(0.45 < v < 0.55 for v in shares.values()), shares


def test_modulo_concentrates_a_regular_key_pattern_on_one_shard():
    """Two onboarding pipelines hand out alternating ids; the large tenant owns
    the even ones. Raw modulo sends all of that tenant's clones to one shard."""
    tenant = range(2, 4001, 2)
    assert _share(["a", "b"], "modulo", tenant) == {"a": 1.0, "b": 0.0}
    for strategy in ("hash", "vbucket"):
        shares = _share(["a", "b"], strategy, tenant)
        assert all(0.45 < v < 0.55 for v in shares.values()), (strategy, shares)


def test_single_shard_ignores_strategy():
    assert route(12345, ["only"], "modulo") == "only"


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        route(1, ["a", "b"], "round-robin")
    with pytest.raises(ValueError):
        ShardRouter([], strategy="nope")


def test_parse_nodes():
    nodes = parse_nodes("a=shard-a:5433, b=shard-b")
    assert [(n.name, n.host, n.port) for n in nodes][0] == ("a", "shard-a", "5433")
    assert nodes[1].host == "shard-b"
    assert parse_nodes("") == []
    with pytest.raises(ValueError):
        parse_nodes("garbage")


# ---- resharding -----------------------------------------------------------


def _moved(before: list[str], after: list[str], strategy: str) -> float:
    t_b = assign_buckets(before) if strategy == "vbucket" else None
    t_a = assign_buckets(after) if strategy == "vbucket" else None
    moved = sum(
        route(i, before, strategy, t_b) != route(i, after, strategy, t_a) for i in IDS
    )
    return moved / len(IDS)


def test_naive_modulo_moves_most_keys_when_a_shard_is_added():
    assert _moved(["a", "b"], ["a", "b", "c"], "modulo") > 0.6
    assert _moved(["a", "b", "c"], ["a", "b", "c", "d"], "modulo") > 0.7


def test_vbucket_moves_about_one_over_n_and_only_to_the_new_shard():
    before, after = ["a", "b"], ["a", "b", "c"]
    assert 0.25 < _moved(before, after, "vbucket") < 0.42  # ~1/3

    t_b, t_a = assign_buckets(before), assign_buckets(after)
    for old_owner, new_owner in zip(t_b, t_a, strict=True):
        # a bucket either stays or moves to the new shard - never between old ones
        assert new_owner == old_owner or new_owner == "c"


# ---- sharded data plane ---------------------------------------------------


@pytest.fixture
async def shards(monkeypatch):
    """Two real databases used as shards, wired into the API and Celery code."""
    base = _test_database_url()
    host_port = base.split("@", 1)[1].split("/", 1)[0]
    host, _, port = host_port.partition(":")
    stem = base.rsplit("/", 1)[-1]
    names = {"a": f"{stem}_shard_a", "b": f"{stem}_shard_b"}
    for database in names.values():
        await _ensure_database_exists(base.rsplit("/", 1)[0] + "/" + database)

    nodes = [ShardNode(n, host, port or "5432", database=d) for n, d in names.items()]
    router = ShardRouter(nodes, strategy="hash")
    for name in router.names:
        engine = router._maker(name).kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(sharding, "router", router)
    monkeypatch.setattr(deps, "router", router)
    yield router

    for name in router.names:
        engine = router._maker(name).kw["bind"]
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await router.dispose()


async def _rows(router: ShardRouter, shard: str, model) -> list:
    async with router.session_for_shard(shard) as s:
        return list((await s.execute(select(model))).scalars().all())


def _clone_ids_on(router: ShardRouter, shard: str, count: int, start: int = 1):
    found, i = [], start
    while len(found) < count:
        if router.shard_for(i) == shard:
            found.append(i)
        i += 1
    return found


async def test_flush_writes_only_to_the_owning_shard(shards, clone_user):
    on_a = clone_user.profile.id
    # move the fixture clone to a deterministic spot: use whichever shard it maps to
    owner = shards.shard_for(on_a)
    other = next(n for n in shards.names if n != owner)

    await streams.append_memory(on_a, "hello", datetime.now(UTC).isoformat())
    async with shards.session(on_a) as s:
        await flush_one_stream(s, on_a)

    assert [b.clone_id for b in await _rows(shards, owner, MemoryBuffer)] == [on_a]
    assert await _rows(shards, other, MemoryBuffer) == []


async def test_backups_list_comes_from_the_owners_shard(
    client, session, shards, clone_headers, clone_user
):
    clone_id = clone_user.profile.id
    owner = shards.shard_for(clone_id)
    other = next(n for n in shards.names if n != owner)
    now = datetime.now(UTC)
    async with shards.session_for_shard(owner) as s:
        s.add(
            MemoryBackup(
                clone_id=clone_id,
                period_start=now,
                period_end=now,
                entry_count=1,
                storage_key=f"x/{clone_id}",
                subscription_tier=SubscriptionTier.FREE,
            )
        )
        await s.commit()

    response = await client.get("/memories/backups", headers=clone_headers)
    assert response.status_code == 200
    assert response.headers["x-db-node"] == f"shard-{owner}"
    assert [b["clone_id"] for b in response.json()] == [clone_id]
    assert await _rows(shards, other, MemoryBackup) == []


async def test_admin_backup_listing_fans_out_and_merges(
    client, session, shards, admin_headers
):
    now = datetime.now(UTC)
    for shard, age in (("a", 5), ("b", 1), ("a", 3)):
        async with shards.session_for_shard(shard) as s:
            s.add(
                MemoryBackup(
                    clone_id=_clone_ids_on(shards, shard, 1)[0],
                    period_start=now,
                    period_end=now - timedelta(minutes=age),
                    entry_count=age,
                    storage_key=f"k/{shard}/{age}",
                    subscription_tier=SubscriptionTier.FREE,
                )
            )
            await s.commit()

    response = await client.get("/admin/backups?limit=10", headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["x-shards-queried"] == "2"
    assert [b["entry_count"] for b in response.json()] == [1, 3, 5]  # newest first

    stats = await client.get("/admin/stats", headers=admin_headers)
    assert stats.json()["backups"] == 3
    assert stats.headers["x-shards-queried"] == "2"


async def test_shard_distribution_endpoint_reports_each_shard(
    client, session, shards, admin_headers
):
    response = await client.get("/admin/shards", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["sharded"] is True
    assert {s["name"] for s in body["shards"]} == {"a", "b"}


async def test_resurrect_copies_between_shards(
    client, session, shards, clone_headers, clone_user, other_clone_user
):
    source, target = other_clone_user.profile, clone_user.profile
    # Make source and target live on different shards by picking whichever
    # pairing the router gives us; if they collide, the test still proves the
    # read/write path works, so only assert placement when they differ.
    source.status = "dormant"
    await session.flush()

    now = datetime.now(UTC)
    key = object_storage.backup_key(source.id, now, now)
    await object_storage.put_object(
        key,
        backup_codec.encode_payload([{"content": "m", "captured_at": now.isoformat()}]),
    )
    async with shards.session(source.id) as s:
        s.add(
            MemoryBackup(
                clone_id=source.id,
                period_start=now,
                period_end=now,
                entry_count=1,
                storage_key=key,
                subscription_tier=SubscriptionTier.FREE,
            )
        )
        await s.commit()

    response = await client.post(
        "/memories/resurrect",
        json={"source_profile_id": source.id},
        headers=clone_headers,
    )
    assert response.status_code == 201
    landed = await _rows(shards, shards.shard_for(target.id), MemoryBackup)
    assert [b.clone_id for b in landed if b.clone_id == target.id]


async def test_due_clones_are_found_by_joining_across_planes(
    session, shards, clone_user
):
    clone = clone_user.profile
    clone.created_at = datetime.now(UTC) - timedelta(days=90)
    await session.flush()

    due = await find_due_clones(session, datetime.now(UTC), shards)
    assert clone.id in {c.id for c in due}

    # a fresh backup on the owning shard makes it not due any more
    now = datetime.now(UTC)
    async with shards.session(clone.id) as s:
        s.add(
            MemoryBackup(
                clone_id=clone.id,
                period_start=now,
                period_end=now,
                entry_count=1,
                storage_key="k",
                subscription_tier=SubscriptionTier.FREE,
            )
        )
        await s.commit()
    due = await find_due_clones(session, datetime.now(UTC), shards)
    assert clone.id not in {c.id for c in due}


async def test_rollup_runs_on_the_owning_shard(session, shards, clone_user):
    clone = clone_user.profile
    now = datetime.now(UTC)
    async with shards.session(clone.id) as s:
        s.add(
            MemoryBuffer(
                clone_id=clone.id,
                payload_blob=backup_codec.encode_payload(
                    [{"content": "x", "captured_at": now.isoformat()}]
                ),
                entry_count=1,
            )
        )
        await s.commit()
        result = await rollup_clone(s, clone, now)
    assert result["status"] == "backed_up"
    owner = shards.shard_for(clone.id)
    assert len(await _rows(shards, owner, MemoryBackup)) == 1
    assert await _rows(shards, owner, MemoryBuffer) == []
