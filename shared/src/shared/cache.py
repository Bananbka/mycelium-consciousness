"""Cache-aside helpers on Redis (lab 4). Fail-open: a dead cache never fails a request.

Key scheme: ``cache:<version>:<domain>:<entity-id>:<params-hash>`` e.g.
``cache:v1:backups:42:9f2c1a7e``. The entity id sits before the params hash so
one entity's every variant can be dropped with a single prefix scan.
"""

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
# Business rule: a clone's backup list may lag reality by at most this long if
# an explicit invalidation is ever missed. Rollups/restores invalidate at once.
BACKUPS_TTL_SECONDS = int(os.getenv("CACHE_BACKUPS_TTL_SECONDS", "60"))
SOCKET_TIMEOUT = float(os.getenv("CACHE_SOCKET_TIMEOUT_SECONDS", "0.5"))
TTL_JITTER = 0.1  # +-10% so keys created together do not expire together

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            CACHE_REDIS_URL,
            decode_responses=True,
            # Fail fast: a stopped Redis must degrade to the DB, not hang.
            socket_connect_timeout=SOCKET_TIMEOUT,
            socket_timeout=SOCKET_TIMEOUT,
        )
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


async def store(key: str, value: Any, ttl: int) -> None:
    jittered = max(1, int(ttl * random.uniform(1 - TTL_JITTER, 1 + TTL_JITTER)))
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
        return len(keys)
    except Exception:
        logger.warning("cache unavailable on invalidate %s", pattern)
        return 0
