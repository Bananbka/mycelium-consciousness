from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from api.deps import CurrentAdmin, DatabaseSession
from api.schemas import MemoryResponse, ProfileResponse, UserResponse
from shared.db.models import CloneProfile, MemoryChunk, User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: CurrentAdmin,
    db: DatabaseSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[User]:
    result = await db.execute(select(User).order_by(User.id).limit(limit))
    return list(result.scalars().all())


@router.get("/clones", response_model=list[ProfileResponse])
async def list_clones(
    _admin: CurrentAdmin,
    db: DatabaseSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[CloneProfile]:
    result = await db.execute(
        select(CloneProfile).order_by(CloneProfile.id).limit(limit)
    )
    return list(result.scalars().all())


@router.get("/clones/{profile_id}/memories", response_model=list[MemoryResponse])
async def list_clone_memories(
    profile_id: int,
    _admin: CurrentAdmin,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryChunk]:
    result = await db.execute(
        select(MemoryChunk)
        .where(MemoryChunk.clone_id == profile_id)
        .order_by(MemoryChunk.timestamp.desc(), MemoryChunk.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: int,
    admin: CurrentAdmin,
    db: DatabaseSession,
) -> User:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    user.is_active = False
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/stats")
async def registry_stats(_admin: CurrentAdmin, db: DatabaseSession) -> dict[str, int]:
    users = await db.scalar(select(func.count()).select_from(User))
    clones = await db.scalar(select(func.count()).select_from(CloneProfile))
    memories = await db.scalar(select(func.count()).select_from(MemoryChunk))
    return {
        "users": users or 0,
        "clones": clones or 0,
        "memories": memories or 0,
    }
