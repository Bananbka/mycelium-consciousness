"""Lab 8: stampede protection, TTL jitter, negative caching, Bloom filter."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared import bloom, cache, guarded_cache


class Counter:
    """An async compute function that records how often it ran."""

    def __init__(self, value="v", delay=0.05):
        self.calls = 0
        self.value = value
        self.delay = delay

    async def __call__(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return self.value


async def _flood(n, key, compute, **kwargs):
    return await asyncio.gather(
        *(
            guarded_cache.get_or_compute("t", key, 30, compute, **kwargs)
            for _ in range(n)
        )
    )


async def test_unprotected_cache_stampedes():
    compute = Counter()
    results = await _flood(50, "cache:v1:t:none", compute, protection="none")
    assert compute.calls == 50  # every concurrent miss computes
    assert {state for _, state in results} == {"MISS"}


async def test_single_flight_collapses_a_burst_to_one_compute():
    compute = Counter()
    results = await _flood(200, "cache:v1:t:lock", compute, protection="lock")
    assert compute.calls == 1
    assert {value for value, _ in results} == {"v"}
    states = {state for _, state in results}
    assert states <= {"MISS", "COALESCED", "HIT"}


async def test_second_burst_is_served_from_cache():
    compute = Counter()
    await _flood(20, "cache:v1:t:warm", compute, protection="lock")
    again = await _flood(20, "cache:v1:t:warm", compute, protection="lock")
    assert compute.calls == 1
    assert {state for _, state in again} == {"HIT"}


async def test_distributed_lock_holds_across_processes():
    """Two 'instances' have separate in-process maps; only the Redis lock can
    keep them from both computing."""
    compute = Counter(delay=0.2)
    key = "cache:v1:t:dist"

    async def instance():
        # simulate a different process: it has never seen the other's task
        return await guarded_cache._lead("t", key, 30, compute, "lock", None)

    a, b = await asyncio.gather(instance(), instance())
    assert compute.calls == 1
    assert {a[0], b[0]} == {"v"}
    assert {a[1], b[1]} == {"MISS", "LOCK_WAIT"}


async def test_dead_leader_cannot_deadlock_waiters(monkeypatch):
    # a leader took the lock and died: the lock key exists, no value ever comes
    client = cache._get_client()
    key = "cache:v1:t:dead"
    await client.set(f"lock:{key}", "ghost", px=60_000)
    monkeypatch.setattr(guarded_cache, "LOCK_WAIT_SECONDS", 0.3)

    compute = Counter(delay=0.01)
    started = time.monotonic()
    value, state = await guarded_cache.get_or_compute(
        "t", key, 30, compute, protection="lock"
    )
    assert value == "v" and state == "MISS"
    assert compute.calls == 1
    assert time.monotonic() - started < 2  # bounded wait, then fail-open


async def test_lock_expiry_allows_a_successor(monkeypatch):
    monkeypatch.setattr(guarded_cache, "LOCK_TTL_MS", 100)
    client = cache._get_client()
    key = "cache:v1:t:expiry"
    await client.set(f"lock:{key}", "ghost", px=100)

    compute = Counter(delay=0.01)
    value, _ = await guarded_cache.get_or_compute(
        "t", key, 30, compute, protection="lock"
    )
    assert value == "v"
    assert await client.exists(f"lock:{key}") == 0  # released by its owner


async def test_release_does_not_delete_someone_elses_lock():
    client = cache._get_client()
    await client.set("lock:x", "successor", px=5000)
    await guarded_cache._release("lock:x", "old-leader-token")
    assert await client.get("lock:x") == "successor"


async def test_compute_error_releases_the_lock_and_propagates():
    async def boom():
        raise RuntimeError("db down")

    key = "cache:v1:t:err"
    with pytest.raises(RuntimeError):
        await guarded_cache.get_or_compute("t", key, 30, boom, protection="lock")
    assert await cache._get_client().exists(f"lock:{key}") == 0
    ok = Counter()
    value, _ = await guarded_cache.get_or_compute("t", key, 30, ok, protection="lock")
    assert value == "v"


async def test_swr_serves_stale_and_refreshes_once(monkeypatch):
    monkeypatch.setattr(guarded_cache, "STALE_SECONDS", 30)
    key = "cache:v1:t:swr"
    first = Counter(value="old")
    await guarded_cache.get_or_compute("t", key, 30, first, protection="swr")

    # age the entry past its soft expiry but inside the stale window
    client = cache._get_client()
    import json

    envelope = json.loads(await client.get(key))
    envelope["exp"] = time.time() - 1
    await client.set(key, json.dumps(envelope), ex=30)

    refresh = Counter(value="new", delay=0.1)
    started = time.monotonic()
    burst = await asyncio.gather(
        *(
            guarded_cache.get_or_compute("t", key, 30, refresh, protection="swr")
            for _ in range(30)
        )
    )
    assert time.monotonic() - started < 0.1  # nobody waited for the recompute
    assert {value for value, _ in burst} == {"old"}
    assert {state for _, state in burst} == {"STALE"}

    await asyncio.sleep(0.4)  # background refresh finishes
    assert refresh.calls == 1
    value, state = await guarded_cache.get_or_compute(
        "t", key, 30, refresh, protection="swr"
    )
    assert (value, state) == ("new", "HIT")


async def test_ttl_jitter_spreads_expiry():
    spread = [cache.jittered_ttl(100, 0.2) for _ in range(500)]
    assert min(spread) >= 80 and max(spread) <= 120
    assert max(spread) - min(spread) > 20  # actually spread, not constant
    assert {cache.jittered_ttl(100, 0.0) for _ in range(20)} == {100.0}


async def test_negative_caching_absorbs_repeated_missing_lookups():
    compute = Counter(value=None, delay=0.0)
    key = "cache:v1:t:missing"
    for _ in range(20):
        value, _ = await guarded_cache.get_or_compute(
            "t", key, 30, compute, protection="lock", cache_negative=True
        )
        assert value is None
    assert compute.calls == 1


async def test_without_negative_caching_every_missing_lookup_hits_the_source():
    compute = Counter(value=None, delay=0.0)
    key = "cache:v1:t:missing2"
    for _ in range(5):
        await guarded_cache.get_or_compute(
            "t", key, 30, compute, protection="lock", cache_negative=False
        )
    assert compute.calls == 5


async def test_metrics_count_database_computations():
    await guarded_cache.reset_metrics("t")
    compute = Counter()
    await _flood(30, "cache:v1:t:metrics", compute, protection="lock")
    counters = await guarded_cache.metrics("t")
    assert counters["computes"] == 1
    assert counters["misses"] + counters.get("hits", 0) >= 1


async def test_xfetch_can_refresh_before_expiry(monkeypatch):
    monkeypatch.setattr(guarded_cache, "XFETCH_BETA", 1000.0)  # always early
    key = "cache:v1:t:xfetch"
    compute = Counter(delay=0.0)
    await guarded_cache.get_or_compute("t", key, 30, compute, protection="lock")
    await guarded_cache.get_or_compute("t", key, 30, compute, protection="lock")
    assert compute.calls >= 1
    assert guarded_cache._early(
        {"exp": time.time() + 5, "d": 1.0, "n": False}, time.time()
    )


async def test_redis_failure_falls_back_to_computing(monkeypatch):
    class Dead:
        async def get(self, *_):
            raise ConnectionError("redis down")

        async def hincrby(self, *_):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "_get_client", lambda: Dead())
    compute = Counter(delay=0.0)
    value, state = await guarded_cache.get_or_compute(
        "t", "cache:v1:t:down", 30, compute, protection="lock"
    )
    assert (value, state) == ("v", "BYPASS")


# ---- Bloom filter -----------------------------------------------------------


async def test_bloom_has_no_false_negatives():
    ids = list(range(1, 2001))
    await bloom.load(ids)
    assert all([await bloom.might_contain(i) for i in ids[:200]])


async def test_bloom_rejects_most_unknown_ids():
    await bloom.load(range(1, 1001))
    unknown = [await bloom.might_contain(i) for i in range(10_000_000, 10_000_500)]
    assert sum(unknown) / len(unknown) < 0.05  # low false-positive rate


async def test_bloom_is_open_until_it_is_loaded():
    # a flushed/empty Redis must never make a real clone look nonexistent
    assert await bloom.might_contain(424242) is True
