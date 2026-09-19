import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import text

from api.config import validate_jwt_secret
from api.deps import DatabaseSession
from api.routers import admin, auth, home, memories, profiles
from shared import object_storage
from shared.db.db import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_jwt_secret()
    await object_storage.ensure_bucket()
    yield
    await dispose_engine()


app = FastAPI(title="Mycelium Consciousness Admin API", lifespan=lifespan)

INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()


@app.middleware("http")
async def add_instance_id(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Instance-ID"] = INSTANCE_ID
    return response


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health_check(db: DatabaseSession):
    result = await db.execute(text("SELECT 1"))
    db_status = "ok" if result.scalar() == 1 else "error"
    return {"status": "healthy", "database": db_status, "instance": INSTANCE_ID}
