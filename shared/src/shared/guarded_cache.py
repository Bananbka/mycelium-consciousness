"""Lab 8: cache-aside that survives a stampede.

``cache.lookup/store`` (lab 4) is the textbook pattern: on a miss every caller
computes the value itself. For a hot, expensive key that is the failure mode
this module exists to remove - the instant the key expires N concurrent
requests all miss, all hit the database, and the database sees N copies of the
same query (cache stampede / thundering herd).

``get_or_compute`` adds, selectable per call (``protection``):

``none``
    Unprotected cache-aside. Kept so the failure can be reproduced and measured.
``lock``
    *Single-flight* inside the process plus a *distributed lock* (Redis
    ``SET NX PX``) across instances: one caller computes, the rest wait for the
    cache to fill. The lock has a TTL (a crashed leader cannot deadlock anyone),
    is released with a token check (a slow leader cannot delete a successor's
    lock) and waiters give up after ``CACHE_LOCK_WAIT_SECONDS`` and compute
    themselves (fail-open: a lock problem degrades to the unprotected path, it
    never turns into an outage).
``swr``
    Stale-while-revalidate: past its soft expiry a value is served immediately
    while exactly one refresh runs in the background (same lock). Users never
    wait for a recompute; the price is bounded staleness.

Orthogonal to the mode:

* **TTL jitter** (``CACHE_TTL_JITTER``): ``ttl * U(1-j, 1+j)`` so keys written
  together do not expire together (avalanche).
* **XFetch** (``CACHE_XFETCH_BETA`` > 0): probabilistic early expiration -
  a request may refresh *before* expiry with probability rising as the expiry
  nears, scaled by how long the computation takes (Vattani et al.).
* **Negative caching**: ``compute`` returning ``None`` means "does not exist";
  with ``cache_negative=True`` that answer is cached for ``negative_ttl`` so a
  flood of requests for a missing id stops reaching the database.

Everything fails open: if Redis is unreachable the value is computed directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from shared import cache

logger = logging.getLogger(__name__)

State = Literal[
    "HIT", "MISS", "COALESCED", "LOCK_WAIT", "STALE", "NEGATIVE_HIT", "BYPASS"
]
PROTECTION_MODES = ("none", "lock", "swr")

DEFAULT_PROTECTION = os.getenv("CACHE_PROTECTION", "lock")
# Must exceed the slowest realistic compute; if it does not the lock expires
# and a second computation starts (duplicate work, never a deadlock).
LOCK_TTL_MS = int(os.getenv("CACHE_LOCK_TTL_MS", "10000"))
LOCK_WAIT_SECONDS = float(os.getenv("CACHE_LOCK_WAIT_SECONDS", "5"))
LOCK_POLL_SECONDS = float(os.getenv("CACHE_LOCK_POLL_SECONDS", "0.025"))
# How long past its soft expiry a value may still be served (swr only).
STALE_SECONDS = int(os.getenv("CACHE_STALE_SECONDS", "30"))
NEGATIVE_TTL_SECONDS = int(os.getenv("CACHE_NEGATIVE_TTL_SECONDS", "30"))
XFETCH_BETA = float(os.getenv("CACHE_XFETCH_BETA", "0"))

# Delete the lock only if we still own it (a slow leader whose lock expired must
# not remove the lock of whoever took over).
_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

_inflight: dict[str, asyncio.Task] = {}
_background: set[asyncio.Task] = set()


# ---- metrics ----------------------------------------------------------------


def _metrics_key(name: str) -> str:
    return f"cache-metrics:{name}"


async def count(name: str, field: str, amount: int = 1) -> None:
    try:
        await cache._get_client().hincrby(_metrics_key(name), field, amount)
    except Exception:
        pass  # metrics must never break a request


async def metrics(name: str) -> dict[str, int]:
    raw = await cache._get_client().hgetall(_metrics_key(name))
    return {k: int(v) for k, v in raw.items()}


async def reset_metrics(name: str) -> None:
    await cache._get_client().delete(_metrics_key(name))


# ---- envelope ---------------------------------------------------------------


async def _read(key: str) -> dict | None:
    raw = await cache._get_client().get(key)
    return None if raw is None else json.loads(raw)


async def _write(
    key: str, value: Any, ttl: float, delta: float, mode: str, negative: bool
) -> None:
    soft = cache.jittered_ttl(ttl)
    hard = soft + (STALE_SECONDS if mode == "swr" and not negative else 0)
    envelope = {
        "v": value,
        "exp": time.time() + soft,
        "d": delta,  # seconds the computation took (XFetch input)
        "n": negative,
    }
    try:
        await cache._get_client().set(
            key, json.dumps(envelope), ex=max(1, math.ceil(hard))
        )
    except Exception:
        logger.warning("cache unavailable on set %s", key)


def _fresh(envelope: dict | None, now: float) -> bool:
    return envelope is not None and now < envelope["exp"]


def _early(envelope: dict, now: float) -> bool:
    """XFetch: refresh early with probability growing as expiry approaches."""
    if XFETCH_BETA <= 0:
        return False
    gap = envelope.get("d", 0.0) * XFETCH_BETA * -math.log(1.0 - random.random())
    return now + gap >= envelope["exp"]


async def invalidate(pattern: str) -> int:
    client = cache._get_client()
    keys = [k async for k in client.scan_iter(match=pattern)]
    if keys:
        await client.delete(*keys)
    return len(keys)


# ---- compute ----------------------------------------------------------------


async def _compute(name: str, compute: Callable[[], Awaitable[Any]]):
    started = time.perf_counter()
    await count(name, "computes")  # one count == one database computation
    value = await compute()
    return value, time.perf_counter() - started


async def _release(key: str, token: str) -> None:
    try:
        await cache._get_client().eval(_RELEASE, 1, key, token)
    except Exception:
        logger.warning("could not release lock %s (it will expire)", key)


async def _lead(
    name: str,
    key: str,
    ttl: float,
    compute: Callable[[], Awaitable[Any]],
    mode: str,
    negative_ttl: float | None,
) -> tuple[Any, State]:
    """Become the single computing caller (distributed lock) or wait for it."""
    client = cache._get_client()
    lock_key = f"lock:{key}"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    waited = False

    async def run_and_store() -> tuple[Any, State]:
        value, delta = await _compute(name, compute)
        is_null = value is None
        if not is_null or negative_ttl:
            await _write(
                key, value, negative_ttl if is_null else ttl, delta, mode, is_null
            )
        return value, "MISS"

    while True:
        try:
            acquired = bool(await client.set(lock_key, token, nx=True, px=LOCK_TTL_MS))
        except Exception:
            await count(name, "lock_errors")
            return await run_and_store()  # Redis unusable: fail open

        if acquired:
            await count(name, "lock_acquired")
            try:
                # Double-check: another instance may have filled the key
                # between our miss and the moment we got the lock.
                current = await _read(key)
                if _fresh(current, time.time()):
                    return current["v"], "HIT"
                return await run_and_store()
            finally:
                await _release(lock_key, token)

        if not waited:
            waited = True
            await count(name, "lock_waits")
        await asyncio.sleep(LOCK_POLL_SECONDS * random.uniform(0.5, 1.5))
        try:
            current = await _read(key)
        except Exception:
            return await run_and_store()
        if _fresh(current, time.time()):
            return current["v"], "LOCK_WAIT"
        if time.monotonic() >= deadline:
            # Leader too slow or lost: do the work ourselves rather than wait
            # forever. Duplicate work is the cost of never deadlocking.
            await count(name, "lock_timeouts")
            return await run_and_store()
        # lock released without a value (leader failed): loop and try to lead


async def _single_flight(
    name: str,
    key: str,
    ttl: float,
    compute: Callable[[], Awaitable[Any]],
    mode: str,
    negative_ttl: float | None,
) -> tuple[Any, State]:
    existing = _inflight.get(key)
    if existing is not None:
        await count(name, "coalesced")
        value, _ = await asyncio.shield(existing)
        return value, "COALESCED"

    task = asyncio.ensure_future(_lead(name, key, ttl, compute, mode, negative_ttl))
    _inflight[key] = task

    def done(t: asyncio.Task) -> None:
        if _inflight.get(key) is t:
            del _inflight[key]
        if not t.cancelled():
            t.exception()  # mark retrieved; callers see it via await

    task.add_done_callback(done)
    # shield: a caller that disconnects must not cancel the computation that
    # other coalesced callers are waiting on.
    return await asyncio.shield(task)


def _refresh_in_background(
    name: str,
    key: str,
    ttl: float,
    compute: Callable[[], Awaitable[Any]],
    mode: str,
    negative_ttl: float | None,
) -> None:
    if key in _inflight:
        return  # a refresh is already running in this process
    task = asyncio.ensure_future(
        _single_flight(name, key, ttl, compute, mode, negative_ttl)
    )
    _background.add(task)

    def done(t: asyncio.Task) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception():
            logger.warning("background refresh of %s failed: %r", key, t.exception())

    task.add_done_callback(done)


# ---- public API -------------------------------------------------------------


async def get_or_compute(
    name: str,
    key: str,
    ttl: float,
    compute: Callable[[], Awaitable[Any]],
    *,
    protection: str | None = None,
    cache_negative: bool = False,
    negative_ttl: float | None = None,
) -> tuple[Any, State]:
    """Return ``(value, state)``; ``value`` is ``None`` for a cached/true miss."""
    mode = protection if protection in PROTECTION_MODES else DEFAULT_PROTECTION
    neg = (negative_ttl or NEGATIVE_TTL_SECONDS) if cache_negative else None

    try:
        envelope = await _read(key)
    except Exception:
        logger.warning("cache unavailable on get %s", key)
        await count(name, "bypass")
        value, _ = await _compute(name, compute)
        return value, "BYPASS"

    now = time.time()
    if _fresh(envelope, now) and not _early(envelope, now):
        await count(name, "hits")
        return envelope["v"], "NEGATIVE_HIT" if envelope["n"] else "HIT"

    if envelope is not None and mode == "swr" and not envelope["n"]:
        # Soft-expired (or XFetch chose to refresh): answer now, refresh behind.
        await count(name, "stale_served")
        _refresh_in_background(name, key, ttl, compute, mode, neg)
        return envelope["v"], "STALE"

    await count(name, "misses")
    if mode == "none":
        value, delta = await _compute(name, compute)
        is_null = value is None
        if not is_null or neg:
            await _write(key, value, neg if is_null else ttl, delta, mode, is_null)
        return value, "MISS"
    return await _single_flight(name, key, ttl, compute, mode, neg)
