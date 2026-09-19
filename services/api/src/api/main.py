import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.config import validate_jwt_secret
from api.deps import DatabaseSession
from api.routers import admin, auth, home, memories, profiles
from shared import object_storage, streams
from shared.db.db import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_jwt_secret()
    await object_storage.ensure_bucket()
    yield
    await dispose_engine()


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


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(admin.router)


async def _check(probe) -> str:
    try:
        await asyncio.wait_for(probe(), timeout=2)
        return "ok"
    except Exception:
        return "error"


async def _probe_db(db) -> None:
    await db.execute(text("SELECT 1"))


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
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            **checks,
            "instance": INSTANCE_ID,
        },
    )
