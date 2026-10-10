from __future__ import annotations

from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select, text

from api.deps import CurrentAdmin, DatabaseSession, ReadDatabaseSession
from api.schemas import (
    BackupResponse,
    ProfileResponse,
    RollupTriggerResponse,
    SubscriptionUpdateRequest,
    UserResponse,
)
from api.tasks import force_rollup_clone
from shared import consistency
from shared.db.db import REPLICA_ENABLED, ReplicaSessionLocal
from shared.db.models import CloneProfile, MemoryBackup, User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _admin: CurrentAdmin,
    db: ReadDatabaseSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[User]:
    result = await db.execute(select(User).order_by(User.id).limit(limit))
    return list(result.scalars().all())


@router.get("/clones", response_model=list[ProfileResponse])
async def list_clones(
    _admin: CurrentAdmin,
    db: ReadDatabaseSession,
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
    db: ReadDatabaseSession,
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
async def registry_stats(
    _admin: CurrentAdmin, db: ReadDatabaseSession
) -> dict[str, int]:
    users = await db.scalar(select(func.count()).select_from(User))
    clones = await db.scalar(select(func.count()).select_from(CloneProfile))
    backups = await db.scalar(select(func.count()).select_from(MemoryBackup))
    return {
        "users": users or 0,
        "clones": clones or 0,
        "backups": backups or 0,
    }


@router.get("/replication")
async def replication_status(_admin: CurrentAdmin, db: DatabaseSession) -> dict:
    """Replication health from both ends: what the primary ships, what the
    standby has replayed. Used to measure lag and to explain a stale read."""
    standbys = (
        (
            await db.execute(
                text(
                    """
                    SELECT application_name, client_addr::text AS client_addr,
                           state, sync_state,
                           pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)::float8
                               AS replay_lag_bytes,
                           EXTRACT(EPOCH FROM replay_lag)::float8 AS replay_lag_seconds
                    FROM pg_stat_replication
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    primary_lsn = await db.scalar(text("SELECT pg_current_wal_lsn()::text"))

    replica: dict = {"enabled": REPLICA_ENABLED, "reachable": False}
    if ReplicaSessionLocal is not None:
        try:
            async with ReplicaSessionLocal() as session:
                row = (
                    (
                        await session.execute(
                            text(
                                """
                                SELECT pg_is_in_recovery() AS in_recovery,
                                       pg_last_wal_replay_lsn()::text AS replay_lsn,
                                       pg_wal_lsn_diff(
                                           pg_last_wal_receive_lsn(),
                                           pg_last_wal_replay_lsn()
                                       )::float8 AS received_not_replayed_bytes,
                                       EXTRACT(EPOCH FROM
                                           now() - pg_last_xact_replay_timestamp())
                                           ::float8
                                           AS seconds_since_last_replayed_commit
                                """
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            replica = {"enabled": True, "reachable": True, **row}
        except consistency.CONNECTIVITY_ERRORS:
            pass

    return {
        "primary_wal_lsn": primary_lsn,
        "standbys": [dict(row) for row in standbys],
        "replica": replica,
        "breaker_open": consistency.replica_breaker.is_open,
        "read_after_write_window_seconds": consistency.STICKY_SECONDS,
    }
