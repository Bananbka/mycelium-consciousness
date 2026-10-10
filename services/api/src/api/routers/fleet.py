"""Lab 8: the fleet-wide read endpoints that sit behind the guarded cache.

* ``GET /fleet/leaderboard`` - the hot, expensive key. One answer for every
  caller (a global aggregate over every shard), so when it expires *all*
  concurrent readers miss at once: the stampede target.
* ``GET /fleet/clones/{id}`` - lookup by id, the penetration target: ids that
  do not exist can never be cached positively.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from itertools import chain
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser
from shared import bloom, cache, guarded_cache, sharding
from shared.db.db import AsyncSessionLocal
from shared.db.models import CloneProfile, MemoryBackup

router = APIRouter(prefix="/fleet", tags=["fleet"])

LEADERBOARD_TTL_SECONDS = int(os.getenv("LEADERBOARD_TTL_SECONDS", "30"))
CLONE_TTL_SECONDS = int(os.getenv("FLEET_CLONE_TTL_SECONDS", "60"))
# Lets an operator pick the protection per request (experiments, demos). Off by
# default: it is a switch that disables a safety mechanism.
ALLOW_OVERRIDE = os.getenv("ALLOW_PROTECTION_OVERRIDE") == "1"
# Experiment knob (like lab 3's SIMULATED_LATENCY_MS): makes the aggregate take
# at least this long on every shard, standing in for a production-sized table.
SIMULATED_QUERY_MS = int(os.getenv("LEADERBOARD_SIMULATED_QUERY_MS", "0"))
PENETRATION_GUARDS = ("none", "negative", "bloom", "both")
DEFAULT_GUARD = os.getenv("PENETRATION_GUARD", "both")


class LeaderboardEntry(BaseModel):
    rank: int
    clone_id: int
    designation: str
    entries: int
    backups: int


class FleetClone(BaseModel):
    id: int
    designation: str
    status: str
    backups: int


class DataAccess:
    """Where the compute functions get their database sessions.

    A compute may run in a background task after the request that started it has
    finished, so it must not borrow the request's session. Tests swap this
    object for one that hands out the test transaction.
    """

    @asynccontextmanager
    async def control(self) -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    async def fan_out(
        self, fn: Callable[[str, AsyncSession], Awaitable[Any]]
    ) -> dict[str, Any]:
        return await sharding.router.fan_out(fn)

    def shard_session(self, clone_id: int):
        return sharding.router.session(clone_id)


data_access = DataAccess()


async def compute_leaderboard(limit: int) -> list[dict]:
    """Top clones by archived entries: a GROUP BY over every shard's backups."""

    async def per_shard(_name: str, session: AsyncSession) -> list[dict]:
        if SIMULATED_QUERY_MS:
            await session.execute(
                text("SELECT pg_sleep(:s)"), {"s": SIMULATED_QUERY_MS / 1000}
            )
        rows = await session.execute(
            text(
                """
                SELECT clone_id,
                       sum(entry_count)::bigint AS entries,
                       count(*)::bigint AS backups
                FROM memory_backups
                GROUP BY clone_id
                ORDER BY entries DESC, clone_id
                LIMIT :n
                """
            ),
            {"n": limit},
        )
        return [dict(r) for r in rows.mappings()]

    parts = await data_access.fan_out(per_shard)
    top = sorted(
        chain.from_iterable(parts.values()),
        key=lambda r: (-r["entries"], r["clone_id"]),
    )[:limit]
    if not top:
        return []
    async with data_access.control() as control:
        names = dict(
            (
                await control.execute(
                    select(CloneProfile.id, CloneProfile.designation).where(
                        CloneProfile.id.in_([r["clone_id"] for r in top])
                    )
                )
            ).all()
        )
    return [
        {
            "rank": i,
            "clone_id": r["clone_id"],
            "designation": names.get(r["clone_id"], f"clone-{r['clone_id']}"),
            "entries": int(r["entries"]),
            "backups": int(r["backups"]),
        }
        for i, r in enumerate(top, start=1)
    ]


async def load_clone(clone_id: int) -> dict | None:
    async with data_access.control() as control:
        profile = await control.get(CloneProfile, clone_id)
    if profile is None:
        return None
    async with data_access.shard_session(clone_id) as shard:
        backups = await shard.scalar(
            select(func.count())
            .select_from(MemoryBackup)
            .where(MemoryBackup.clone_id == clone_id)
        )
    return {
        "id": profile.id,
        "designation": profile.designation,
        "status": profile.status,
        "backups": int(backups or 0),
    }


def _protection(request: Request) -> str | None:
    if not ALLOW_OVERRIDE:
        return None
    return request.headers.get("X-Cache-Protection", "").lower() or None


def _guard(request: Request) -> str:
    if ALLOW_OVERRIDE:
        requested = request.headers.get("X-Penetration-Guard", "").lower()
        if requested in PENETRATION_GUARDS:
            return requested
    return DEFAULT_GUARD if DEFAULT_GUARD in PENETRATION_GUARDS else "both"


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(
    _user: CurrentUser,
    request: Request,
    response: Response,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[dict]:
    key = cache.make_key("fleet-leaderboard", 0, limit=limit)
    protection = _protection(request)
    value, state = await guarded_cache.get_or_compute(
        "leaderboard",
        key,
        LEADERBOARD_TTL_SECONDS,
        lambda: compute_leaderboard(limit),
        protection=protection,
    )
    response.headers["X-Cache"] = state
    response.headers["X-Cache-Protection"] = (
        protection or guarded_cache.DEFAULT_PROTECTION
    )
    return value


@router.get("/clones/{clone_id}", response_model=FleetClone)
async def read_fleet_clone(
    clone_id: int,
    _user: CurrentUser,
    request: Request,
    response: Response,
) -> dict:
    guard = _guard(request)
    response.headers["X-Penetration-Guard"] = guard

    if guard in ("bloom", "both") and not await bloom.might_contain(clone_id):
        await guarded_cache.count("fleet-clone", "bloom_rejects")
        response.headers["X-Cache"] = "BLOOM_REJECT"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
            headers={"X-Cache": "BLOOM_REJECT", "X-Penetration-Guard": guard},
        )

    key = cache.make_key("fleet-clone", clone_id)
    value, state = await guarded_cache.get_or_compute(
        "fleet-clone",
        key,
        CLONE_TTL_SECONDS,
        lambda: load_clone(clone_id),
        protection=_protection(request),
        cache_negative=guard in ("negative", "both"),
    )
    response.headers["X-Cache"] = state
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
            headers={"X-Cache": state, "X-Penetration-Guard": guard},
        )
    return value
