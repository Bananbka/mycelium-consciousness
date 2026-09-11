from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.db import get_db

app = FastAPI(title="Mycelium Consciousness Admin API")
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


@app.get("/health")
async def health_check(db: DatabaseSession):
    result = await db.execute(text("SELECT 1"))
    db_status = "ok" if result.scalar() == 1 else "error"
    return {"status": "healthy", "database": db_status}
