from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from typing import Any, Literal

import redis.asyncio as redis

logger = logging.getLogger(__name__)

CacheState = Literal["HIT", "MISS", "BYPASS"]

CACHE_REDIS_URL = os.getenv(
    "CACHE_REDIS_URL",
    os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0").rsplit("/", 1)[0] + "/1",
)
KEY_VERSION = "v1"
BACKUPS_TTL_SECONDS = int(os.getenv("CACHE_BACKUPS_TTL_SECONDS", "60"))
SOCKET_TIMEOUT = float(os.getenv("CACHE_SOCKET_TIMEOUT_SECONDS", "0.5"))
TTL_JITTER = float(os.getenv("CACHE_TTL_JITTER", "0.1"))
POOL_SIZE = int(os.getenv("CACHE_POOL_SIZE", "50"))
POOL_WAIT_SECONDS = float(os.getenv("CACHE_POOL_WAIT_SECONDS", "2"))

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        # A *bounded, blocking* pool. An unbounded one dials a new socket for
        # every concurrent request; under a burst those connects blow through
        # SOCKET_TIMEOUT, every caller "fails open" to the database and the
        # cache outage becomes exactly the stampede it was meant to prevent.
        pool = redis.BlockingConnectionPool.from_url(
            CACHE_REDIS_URL,
            decode_responses=True,
            max_connections=POOL_SIZE,
            timeout=POOL_WAIT_SECONDS,
            socket_connect_timeout=SOCKET_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
        )
        _client = redis.Redis(connection_pool=pool)
    return _client


def make_key(domain: str, entity_id: int, **params: Any) -> str:
    digest = hashlib.sha1(
        json.dumps(params, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]
    return f"cache:{KEY_VERSION}:{domain}:{entity_id}:{digest}"


async def lookup(key: str) -> tuple[CacheState, Any]:
    try:
        raw = await _get_client().get(key)
    except Exception:
        logger.warning("cache unavailable on get %s", key, exc_info=True)
        return "BYPASS", None
    if raw is None:
        return "MISS", None
    return "HIT", json.loads(raw)


def jittered_ttl(ttl: float, jitter: float | None = None) -> float:
    """TTL = base +/- jitter*base, uniformly. Keeps keys written together from
    expiring together (the cause of a cache avalanche)."""
    j = TTL_JITTER if jitter is None else jitter
    return max(1.0, ttl * random.uniform(1 - j, 1 + j))


async def store(key: str, value: Any, ttl: int) -> None:
    jittered = int(jittered_ttl(ttl))
    try:
        await _get_client().set(key, json.dumps(value), ex=jittered)
    except Exception:
        logger.warning("cache unavailable on set %s", key)


async def invalidate_backups(clone_id: int) -> int:
    """Drop every cached backup listing for one clone. Never raises."""
    pattern = f"cache:{KEY_VERSION}:backups:{clone_id}:*"
    client = _get_client()
    try:
        keys = [key async for key in client.scan_iter(match=pattern)]
        if keys:
            await client.delete(*keys)
        logger.info("cache invalidated clone_id=%d keys=%d", clone_id, len(keys))
        return len(keys)
    except Exception:
        logger.warning("cache unavailable on invalidate %s", pattern)
        return 0
