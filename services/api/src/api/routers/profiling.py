"""Lab 9: layer-isolation endpoints, mounted only with PROFILING_ENDPOINTS=1.

Each one adds exactly one layer on top of the previous, so the cost of a layer
is a throughput difference, not a guess (``profiling/README.md``)::

    /_prof/noop    framework + middleware only (no auth, no I/O)
    /_prof/redis   + one Redis round trip
    /_prof/db      + one pooled Postgres checkout and ``SELECT 1``
    /_prof/auth    + JWT verification and the user/profile lookup (``/me/home``
                     without the response model)

They are never part of the public API and are absent unless the flag is set.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from api.deps import CurrentUser, DatabaseSession
from shared import streams

router = APIRouter(prefix="/_prof", tags=["profiling"], include_in_schema=False)


@router.get("/noop")
async def noop() -> dict[str, bool]:
    return {"ok": True}


@router.get("/redis")
async def redis_ping() -> dict[str, bool]:
    await streams.get_redis().ping()
    return {"ok": True}


@router.get("/db")
async def db_ping(db: DatabaseSession) -> dict[str, bool]:
    await db.execute(text("SELECT 1"))
    return {"ok": True}


@router.get("/auth")
async def auth_only(user: CurrentUser) -> dict[str, int]:
    return {"id": user.id}
