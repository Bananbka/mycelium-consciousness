import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from api import observability
from api.config import validate_jwt_secret
from api.deps import DatabaseSession
from api.routers import admin, auth, home, memories, profiles
from shared import object_storage
from shared.db.db import dispose_engine

APP_VERSION = os.getenv("APP_VERSION", "dev")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_jwt_secret()
    await object_storage.ensure_bucket()
    yield
    await dispose_engine()


app = FastAPI(title="Mycelium Consciousness Admin API", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(admin.router)
observability.install(app)


@app.get("/health", tags=["health"])
async def health_check(db: DatabaseSession):
    result = await db.execute(text("SELECT 1"))
    db_status = "ok" if result.scalar() == 1 else "error"
    return {"status": "healthy", "database": db_status, "version": APP_VERSION}


@app.get("/version", tags=["health"])
async def version():
    return {"version": APP_VERSION}
