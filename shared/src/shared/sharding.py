"""Lab 7: shard router for the data plane (``memory_buffers``, ``memory_backups``).

Shard key: ``clone_id`` (the clone's profile id). Every data-plane access in
this system is per clone - a write drains one clone's stream into one buffer
row, a rollup reads one buffer and writes that clone's backups, a listing reads
one clone's backups - so a clone's rows always live together on one shard and
those operations never leave it. The identity tables (``users``,
``clone_profiles``) are small, read-mostly and queried by other keys (email,
user id, designation); they stay on the control-plane database.

Routing strategies (``SHARD_STRATEGY``):

``modulo``
    ``shards[clone_id % N]``. Fixed-width arithmetic on the raw key: trivially
    cheap, but it inherits any regularity in the id sequence and a change of
    ``N`` re-homes almost every key.
``hash``
    ``shards[hash(clone_id) % N]``. Breaks up id regularities; resharding is
    still near-total because the divisor changes.
``vbucket`` (default)
    ``hash(clone_id) % 1024`` selects a *virtual bucket*; a bucket -> shard
    table says where each bucket lives. Adding a shard moves only the buckets
    reassigned to it (~1/N of the data) and individual hot buckets can be
    moved on their own. The table is derived with rendezvous (highest random
    weight) hashing, so every API and Celery process computes the same table
    from the shard list alone - no coordination service.

All strategies are deterministic functions of ``(clone_id, shard list)``; the
shard list order must be identical on every process (it comes from one env var).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.db import db

logger = logging.getLogger(__name__)

T = TypeVar("T")

VBUCKETS = int(os.getenv("SHARD_VBUCKETS", "1024"))
STRATEGIES = ("modulo", "hash", "vbucket")
DEFAULT_SHARD = "default"


def stable_hash(value: int | str) -> int:
    """A 64-bit hash that is identical across processes and Python versions.

    ``hash()`` is salted per process and would route the same key differently
    in every API worker, so it must never be used for placement.
    """
    raw = str(value).encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")


def assign_buckets(shards: list[str], buckets: int = VBUCKETS) -> list[str]:
    """Rendezvous hashing: each bucket goes to the shard with the highest
    ``hash(shard, bucket)``. Adding a shard steals ~1/N buckets from the others
    and leaves every other bucket where it was."""
    return [
        max(shards, key=lambda shard: stable_hash(f"{shard}:{bucket}"))
        for bucket in range(buckets)
    ]


def route(
    clone_id: int,
    shards: list[str],
    strategy: str,
    bucket_table: list[str] | None = None,
) -> str:
    if len(shards) == 1:
        return shards[0]
    if strategy == "modulo":
        return shards[clone_id % len(shards)]
    if strategy == "hash":
        return shards[stable_hash(clone_id) % len(shards)]
    if strategy == "vbucket":
        table = bucket_table or assign_buckets(shards)
        return table[stable_hash(clone_id) % len(table)]
    raise ValueError(f"unknown shard strategy {strategy!r}")


@dataclass(frozen=True)
class ShardNode:
    name: str
    host: str
    port: str
    database: str | None = None  # defaults to POSTGRES_DB, same as every node

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{db.POSTGRES_USER}:{db.POSTGRES_PASSWORD}"
            f"@{self.host}:{self.port}/{self.database or db.POSTGRES_DB}"
        )


def parse_nodes(spec: str) -> list[ShardNode]:
    """``a=shard-a:5432,b=shard-b`` -> nodes; port defaults to POSTGRES_PORT."""
    nodes = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        name, _, address = part.partition("=")
        host, _, port = address.partition(":")
        if not name or not host:
            raise ValueError(f"bad SHARD_NODES entry {part!r}, want name=host[:port]")
        nodes.append(ShardNode(name, host, port or db.POSTGRES_PORT))
    return nodes


class ShardRouter:
    """Maps a shard key to a database and hands out sessions on it.

    With no nodes configured it is a transparent single-database router that
    reuses the application's primary engine, so ``SHARD_NODES=`` reproduces the
    pre-sharding behaviour exactly.
    """

    def __init__(
        self,
        nodes: list[ShardNode],
        strategy: str = "vbucket",
        pool_size: int = 5,
        max_overflow: int = 2,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown shard strategy {strategy!r}")
        self.strategy = strategy
        self.nodes = {n.name: n for n in nodes}
        self.names = [n.name for n in nodes] or [DEFAULT_SHARD]
        self.sharded = len(nodes) > 1
        self._table = assign_buckets(self.names) if strategy == "vbucket" else None
        self._pool = (pool_size, max_overflow)
        self._makers: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._engines: list = []

    # ---- placement -------------------------------------------------------

    def shard_for(self, clone_id: int) -> str:
        return route(clone_id, self.names, self.strategy, self._table)

    def bucket_for(self, clone_id: int) -> int:
        return stable_hash(clone_id) % VBUCKETS

    def bucket_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(self.names, 0)
        for shard in self._table or []:
            counts[shard] += 1
        return counts

    # ---- connections -----------------------------------------------------

    def _maker(self, name: str) -> async_sessionmaker[AsyncSession]:
        if name not in self._makers:
            if not self.sharded:
                self._makers[name] = db.AsyncSessionLocal
            else:
                engine = create_async_engine(
                    self.nodes[name].url,
                    pool_size=self._pool[0],
                    max_overflow=self._pool[1],
                    pool_pre_ping=True,
                    pool_recycle=db.POOL_RECYCLE_SECONDS,
                    connect_args={"timeout": db.CONNECT_TIMEOUT_SECONDS},
                )
                self._engines.append(engine)
                self._makers[name] = async_sessionmaker(
                    bind=engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autoflush=False,
                )
        return self._makers[name]

    def session_for_shard(self, name: str) -> AsyncSession:
        return self._maker(name)()

    @asynccontextmanager
    async def session(self, clone_id: int) -> AsyncIterator[AsyncSession]:
        async with self.session_for_shard(self.shard_for(clone_id)) as session:
            yield session

    async def fan_out(
        self, fn: Callable[[str, AsyncSession], Awaitable[T]]
    ) -> dict[str, T]:
        """Scatter-gather: run ``fn`` against every shard concurrently."""

        async def one(name: str) -> tuple[str, T]:
            async with self.session_for_shard(name) as session:
                return name, await fn(name, session)

        return dict(await asyncio.gather(*(one(n) for n in self.names)))

    async def dispose(self) -> None:
        for engine in self._engines:
            await engine.dispose()
        self._engines.clear()
        self._makers.clear()


def _from_env() -> ShardRouter:
    return ShardRouter(
        parse_nodes(os.getenv("SHARD_NODES", "")),
        strategy=os.getenv("SHARD_STRATEGY", "vbucket"),
        pool_size=int(os.getenv("SHARD_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("SHARD_MAX_OVERFLOW", "2")),
    )


router = _from_env()
