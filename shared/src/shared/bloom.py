"""Lab 8: a Bloom filter of existing clone ids, kept in Redis.

Negative caching stops *repeated* requests for the same missing id from
reaching the database, but a flood of requests for *distinct* missing ids
(every one a first-time miss) still goes straight through it. A Bloom filter
answers "this id has definitely never existed" from a 128 KiB bit array without
touching the database at all.

Properties that shape how it is used:

* No false negatives, so a ``False`` from :func:`might_contain` is final.
* False positives (default ~1% at 100k ids) just fall through to the normal
  cache/database path, where negative caching handles them.
* It is only ever *added to*: ids are never removed (Bloom filters cannot
  delete), which is fine because clone ids are never reused.
* Redis is a cache here, not the source of truth. Until a ``ready`` marker says
  the filter has been fully loaded from ``clone_profiles`` it answers "maybe",
  so a flushed or empty Redis can never make a real clone look nonexistent.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable

from shared import cache

logger = logging.getLogger(__name__)

BITS = int(os.getenv("BLOOM_BITS", str(1 << 20)))
HASHES = int(os.getenv("BLOOM_HASHES", "4"))
KEY = "bloom:v1:clones"
READY_KEY = f"{KEY}:ready"


def _positions(item: int) -> list[int]:
    digest = hashlib.blake2b(str(item).encode(), digest_size=16).digest()
    h1 = int.from_bytes(digest[:8], "big")
    h2 = int.from_bytes(digest[8:], "big") | 1  # odd step: visits distinct bits
    return [(h1 + i * h2) % BITS for i in range(HASHES)]


async def add(items: Iterable[int]) -> None:
    try:
        pipe = cache._get_client().pipeline(transaction=False)
        for item in items:
            for pos in _positions(item):
                pipe.setbit(KEY, pos, 1)
        await pipe.execute()
    except Exception:
        logger.warning("bloom filter unavailable on add")


async def mark_ready() -> None:
    try:
        await cache._get_client().set(READY_KEY, "1")
    except Exception:
        logger.warning("bloom filter unavailable on mark_ready")


async def is_ready() -> bool:
    try:
        return bool(await cache._get_client().exists(READY_KEY))
    except Exception:
        return False


async def might_contain(item: int) -> bool:
    """False = definitely absent. True = present or *unknown* (fail-open)."""
    try:
        client = cache._get_client()
        if not await client.exists(READY_KEY):
            return True
        pipe = client.pipeline(transaction=False)
        for pos in _positions(item):
            pipe.getbit(KEY, pos)
        return all(await pipe.execute())
    except Exception:
        return True


async def load(ids: Iterable[int]) -> int:
    """(Re)build the filter from the source of truth and mark it usable."""
    ids = list(ids)
    await add(ids)
    await mark_ready()
    return len(ids)
