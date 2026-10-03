import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.config import validate_jwt_secret
from api.deps import DatabaseSession
from api.routers import admin, auth, home, memories, profiles
from shared import object_storage
from shared.db.db import dispose_engine
from shared.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_jwt_secret()
    await object_storage.ensure_bucket()
    yield
    await dispose_engine()


app = FastAPI(
    title="Mycelium Consciousness Admin API",
    lifespan=lifespan,
    debug=settings.debug,
    # Interactive docs enumerate every endpoint; keep them off in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)


async def unhandled_exception_handler(request: Request, exc: Exception):
    """Answer 500 with an opaque body; the traceback goes to the log only.

    The error id lets an operator find the matching log entry when a user
    reports it, without the response leaking file paths, SQL or library
    internals.
    """
    error_id = uuid.uuid4().hex
    logger.error(
        "Unhandled error %s on %s %s",
        error_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_id": error_id},
    )


# A registered Exception handler takes precedence over Starlette's debug page,
# so it is only installed when DEBUG is off (always the case in production).
if not settings.debug:
    app.add_exception_handler(Exception, unhandled_exception_handler)


app.include_router(auth.router)
app.include_router(home.router)
app.include_router(profiles.router)
app.include_router(memories.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health_check(db: DatabaseSession):
    result = await db.execute(text("SELECT 1"))
    db_status = "ok" if result.scalar() == 1 else "error"
    return {"status": "healthy", "database": db_status}
