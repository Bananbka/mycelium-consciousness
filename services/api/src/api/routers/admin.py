from __future__ import annotations

from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from api.deps import CurrentAdmin, DatabaseSession
from api.schemas import (
    BackupResponse,
    ProfileResponse,
    RollupTriggerResponse,
    SubscriptionUpdateRequest,
    UserResponse,
)
from api.tasks import force_rollup_clone
from shared.db.models import CloneProfile, MemoryBackup, User

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


@router.patch("/clones/{profile_id}/subscription", response_model=ProfileResponse)
async def update_subscription(
    profile_id: int,
    payload: SubscriptionUpdateRequest,
    _admin: CurrentAdmin,
    db: DatabaseSession,
) -> CloneProfile:
    profile = await db.get(CloneProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    profile.subscription_tier = payload.subscription_tier
    await db.commit()
    await db.refresh(profile)
    return profile


@router.get("/clones/{profile_id}/backups", response_model=list[BackupResponse])
async def list_clone_backups(
    profile_id: int,
    _admin: CurrentAdmin,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryBackup]:
    result = await db.execute(
        select(MemoryBackup)
        .where(MemoryBackup.clone_id == profile_id)
        .order_by(MemoryBackup.period_end.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/clones/{profile_id}/rollup", response_model=RollupTriggerResponse)
async def trigger_rollup(
    profile_id: int,
    _admin: CurrentAdmin,
    db: DatabaseSession,
) -> dict[str, str | int]:
    """Roll up one clone immediately, bypassing its tier's due date.

    For testing and ops use; the scheduled path is celery-beat's hourly (by
    default) check against each clone's subscription tier.
    """
    profile = await db.get(CloneProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        return await run_in_threadpool(force_rollup_clone, profile_id)
    except CeleryTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Rollup did not complete in time",
        ) from exc


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
    backups = await db.scalar(select(func.count()).select_from(MemoryBackup))
    return {
        "users": users or 0,
        "clones": clones or 0,
        "backups": backups or 0,
    }
