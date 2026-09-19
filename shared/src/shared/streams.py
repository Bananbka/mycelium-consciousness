from __future__ import annotations

import os

import redis.asyncio as redis

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
STREAM_KEY_PREFIX = "clone-stream:"

# Safety valve, not a target: a single clone's stream should be drained by
# the periodic flush (STREAM_FLUSH_INTERVAL_SECONDS) long before this many
# entries pile up. Reaching it triggers an immediate out-of-band flush for
# that one clone instead of silently discarding anything.
MEMORY_STREAM_MAXLEN = int(os.getenv("MEMORY_STREAM_MAXLEN", "10000"))

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def stream_key(clone_id: int) -> str:
    return f"{STREAM_KEY_PREFIX}{clone_id}"


async def append_memory(
    clone_id: int, content: str, captured_at: str
) -> tuple[str, int]:
    client = get_redis()
    key = stream_key(clone_id)
    entry_id = await client.xadd(key, {"content": content, "captured_at": captured_at})
    length = await client.xlen(key)
    return entry_id, length


async def read_all(clone_id: int) -> list[tuple[str, dict]]:
    client = get_redis()
    entries = await client.xrange(stream_key(clone_id))
    return [(entry_id, fields) for entry_id, fields in entries]


async def trim_up_to(clone_id: int, last_entry_id: str) -> None:
    ms, seq = last_entry_id.split("-")
    next_id = f"{ms}-{int(seq) + 1}"

    client = get_redis()
    await client.xtrim(stream_key(clone_id), minid=next_id)


async def stream_length(clone_id: int) -> int:
    client = get_redis()
    return await client.xlen(stream_key(clone_id))


async def active_clone_ids() -> list[int]:
    client = get_redis()
    ids = []
    async for key in client.scan_iter(match=f"{STREAM_KEY_PREFIX}*"):
        ids.append(int(key.removeprefix(STREAM_KEY_PREFIX)))
    return ids
