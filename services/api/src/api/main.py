import asyncio
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg.exceptions as pg_exc
import redis.exceptions as redis_exc
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    InternalError,
    OperationalError,
)
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class InstanceHeaderMiddleware:
    """Label every response with the serving instance (and apply lab 3 latency).

    Plain ASGI instead of ``@app.middleware("http")``: ``BaseHTTPMiddleware``
    runs the rest of the stack in a separate task behind memory streams, which
    the lab 9/10 profiles showed as a per-request cost of its own.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if SIMULATED_LATENCY_MS and scope["path"] != "/health":
            await asyncio.sleep(SIMULATED_LATENCY_MS / 1000)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Instance-ID"] = INSTANCE_ID
            await send(message)

        await self.app(scope, receive, send_with_header)


class ReadAfterWriteMiddleware:
    """Open the writer's read-after-write window and label the serving node.

    ``scope["state"]`` is what ``request.state`` wraps, so the dependencies in
    ``api.deps`` can tell this middleware who wrote and which node answered.
    The window is opened while handling ``http.response.start`` - before the
    first byte reaches the client - so a client that fires its next read
    immediately cannot overtake it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]

        async def send_marked(message: Message) -> None:
            if message["type"] == "http.response.start":
                state = scope.get("state") or {}
                user_id = state.get("user_id")
                if (
                    REPLICA_ENABLED
                    and user_id is not None
                    and method not in SAFE_METHODS
                    and message["status"] < 400
                ):
                    await consistency.mark_user_write(user_id)
                if node := state.get("db_node"):
                    MutableHeaders(scope=message)["X-DB-Node"] = node
            await send(message)

        await self.app(scope, receive, send_marked)


# Added in this order so the read-after-write layer is the outermost.
app.add_middleware(InstanceHeaderMiddleware)
app.add_middleware(ReadAfterWriteMiddleware)


@app.exception_handler(ConnectionError)
@app.exception_handler(socket.gaierror)
@app.exception_handler(TimeoutError)
@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(InternalError)
@app.exception_handler(DBAPIError)
@app.exception_handler(pg_exc.PostgresConnectionError)
@app.exception_handler(pg_exc.CannotConnectNowError)
@app.exception_handler(pg_exc.InterfaceError)
async def database_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    """A node needed by this request is down: say so (503), do not 500.

    Anything that escapes as a bare 500 closes the connection towards nginx,
    which counts it against ``max_fails`` - with two instances, three of those
    in 15 s take the whole service out of rotation (lab 10).
    """
    if (
        isinstance(exc, DBAPIError)
        and not exc.connection_invalidated
        and not isinstance(exc, OperationalError | InterfaceError | InternalError)
    ):
        # a real SQL error (constraint, syntax...), not a lost node
        return JSONResponse(
            status_code=500, content={"detail": "Internal server error"}
        )
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable"},
        headers={"Retry-After": "5"},
    )


@app.exception_handler(redis_exc.RedisError)
async def buffer_unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Redis (the write buffer, lock and Bloom store) is down: 503, not 500.

    Reads that only use Redis as a cache never get here (they bypass it); this
    is the write path, which has no other place to put an entry. A bare 500
    would reset the connection and push nginx to drop the instance (lab 10).
    """
    return JSONResponse(
        status_code=503,
        content={"detail": "Memory buffer unavailable"},
        headers={"Retry-After": "5"},
    )


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(fleet.router)
app.include_router(admin.router)

if os.getenv("PROFILING_ENDPOINTS") == "1":  # lab 9 layer-isolation probes
    from api.routers import profiling

    app.include_router(profiling.router)


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
