from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.db import dispose_engine, get_db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


app = FastAPI(title="Mycelium Consciousness Admin API", lifespan=lifespan)
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@app.get("/health")
async def health_check(db: DatabaseSession):
    result = await db.execute(text("SELECT 1"))
    db_status = "ok" if result.scalar() == 1 else "error"
    return {"status": "healthy", "database": db_status}
