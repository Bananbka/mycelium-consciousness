"""Small Redis read-through cache for hot, read-mostly lookups.

Two things are cached (see `docs/optimization-report.md`):

* the authenticated principal (user + profile) that every request would
  otherwise re-read from Postgres, keyed by user id;
* a clone's backup listing, keyed by clone id (one hash, one field per `limit`).

Entries expire after a TTL (`AUTH_CACHE_TTL_SECONDS`, `BACKUPS_CACHE_TTL_SECONDS`;
0 disables a cache) and are *also* deleted explicitly by every code path that
changes the underlying rows, so the TTL is only a backstop against a missed
invalidation or a concurrent write racing a refill. Redis being down degrades to
a cache miss, never to a failed request.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from shared.streams import get_redis

logger = logging.getLogger(__name__)

PREFIX = "cache:"


def principal_key(user_id: int) -> str:
    return f"{PREFIX}principal:{user_id}"


def backups_key(clone_id: int) -> str:
    return f"{PREFIX}backups:{clone_id}"


async def get_json(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
    except RedisError:
        logger.warning("cache get failed for %s", key, exc_info=True)
        return None
    return None if raw is None else json.loads(raw)


async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value), ex=ttl_seconds)
    except RedisError:
        logger.warning("cache set failed for %s", key, exc_info=True)


async def get_field(key: str, field: str) -> Any | None:
    try:
        raw = await get_redis().hget(key, field)
    except RedisError:
        logger.warning("cache hget failed for %s", key, exc_info=True)
        return None
    return None if raw is None else json.loads(raw)


async def set_field(key: str, field: str, value: Any, ttl_seconds: int) -> None:
    try:
        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, field, json.dumps(value))
            pipe.expire(key, ttl_seconds)
            await pipe.execute()
    except RedisError:
        logger.warning("cache hset failed for %s", key, exc_info=True)


async def delete(*keys: str) -> None:
    try:
        await get_redis().delete(*keys)
    except RedisError:
        # The TTL still bounds how long a stale entry can live.
        logger.warning("cache invalidation failed for %s", keys, exc_info=True)


async def invalidate_principal(user_id: int | None) -> None:
    if user_id is not None:
        await delete(principal_key(user_id))


async def invalidate_backups(clone_id: int) -> None:
    await delete(backups_key(clone_id))
