import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import InterfaceError, OperationalError

from api.config import validate_jwt_secret
from api.deps import SAFE_METHODS, DatabaseSession
from api.routers import admin, auth, fleet, home, memories, profiles
from shared import bloom, consistency, object_storage, sharding, streams
from shared.db.db import (
    REPLICA_ENABLED,
    AsyncSessionLocal,
    ReplicaSessionLocal,
    dispose_engine,
)
from shared.db.models import CloneProfile

logger = logging.getLogger(__name__)


async def _load_bloom_filter() -> None:
    """Fill the existing-clone Bloom filter once (idempotent across replicas)."""
    try:
        if await bloom.is_ready():
            return
        async with AsyncSessionLocal() as session:
            ids = (await session.execute(select(CloneProfile.id))).scalars().all()
        await bloom.load(ids)
    except Exception:
        logger.warning("bloom filter not loaded; lookups fall back to the cache")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_jwt_secret()
    await object_storage.ensure_bucket()
    await _load_bloom_filter()
    yield
    await dispose_engine()
    await sharding.router.dispose()


app = FastAPI(title="Mycelium Consciousness Admin API", lifespan=lifespan)

INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()
# Lab 3 experiment knob: makes one deployment artificially slow.
SIMULATED_LATENCY_MS = int(os.getenv("SIMULATED_LATENCY_MS", "0"))


@app.middleware("http")
async def add_instance_id(request: Request, call_next):
    if SIMULATED_LATENCY_MS and request.url.path != "/health":
        await asyncio.sleep(SIMULATED_LATENCY_MS / 1000)
    response = await call_next(request)
    response.headers["X-Instance-ID"] = INSTANCE_ID
    return response


@app.middleware("http")
async def read_after_write(request: Request, call_next):
    """Open the writer's read-after-write window and label the serving node.

    ``request.state`` is shared with the route via the ASGI scope, so the
    dependencies in ``api.deps`` can tell this middleware who wrote and which
    node answered. The flag is set *before* the response is returned, so a
    client that fires its next read immediately cannot overtake it.
    """
    response = await call_next(request)
    user_id = getattr(request.state, "user_id", None)
    if (
        REPLICA_ENABLED
        and user_id is not None
        and request.method not in SAFE_METHODS
        and response.status_code < 400
    ):
        await consistency.mark_user_write(user_id)
    if node := getattr(request.state, "db_node", None):
        response.headers["X-DB-Node"] = node
    return response


@app.exception_handler(ConnectionError)
@app.exception_handler(socket.gaierror)
@app.exception_handler(TimeoutError)
@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """A node needed by this request is down: say so (503), do not 500."""
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable"},
        headers={"Retry-After": "5"},
    )


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(fleet.router)
app.include_router(admin.router)


async def _check(probe) -> str:
    try:
        await asyncio.wait_for(probe(), timeout=2)
        return "ok"
    except Exception:
        return "error"


async def _probe_db(db) -> None:
    await db.execute(text("SELECT 1"))


async def _probe_shard(name: str) -> None:
    async with sharding.router.session_for_shard(name) as session:
        await session.execute(text("SELECT 1"))


async def _probe_replica() -> None:
    async with ReplicaSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _probe_redis() -> None:
    await streams.get_redis().ping()


async def _probe_minio() -> None:
    await object_storage.list_keys("__health__/")


@app.get("/health", tags=["health"])
async def health_check(db: DatabaseSession):
    checks = {
        "database": await _check(lambda: _probe_db(db)),
        "redis": await _check(_probe_redis),
        "object_storage": await _check(_probe_minio),
    }
    if sharding.router.sharded:
        # The data plane is critical: a shard that cannot answer means every
        # clone routed to it is down, even though the API itself is fine.
        for name in sharding.router.names:
            checks[f"shard_{name}"] = await _check(lambda n=name: _probe_shard(n))
    healthy = all(v == "ok" for v in checks.values())
    # Informational: a down standby degrades read capacity, not availability
    # (reads fall back to the primary), so it must not pull the node out of
    # the load balancer.
    replica = await _check(_probe_replica) if REPLICA_ENABLED else "disabled"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            **checks,
            "replica": replica,
            "instance": INSTANCE_ID,
        },
    )
