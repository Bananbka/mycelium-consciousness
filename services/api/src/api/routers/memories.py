from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from api.deps import DatabaseSession, OwnBackup, OwnProfile
from api.schemas import (
    BackupDetailResponse,
    BackupResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    ResurrectRequest,
)
from api.tasks import flush_clone_stream
from shared import backup_codec, object_storage, streams
from shared.db.models import ACTIVE_STATUS, CloneProfile, MemoryBackup

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post(
    "/write",
    response_model=MemoryWriteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def write_memory(
    payload: MemoryWriteRequest,
    profile: OwnProfile,
) -> MemoryWriteResponse:
    captured_at = payload.captured_at or datetime.now(UTC)
    _, length = await streams.append_memory(
        profile.id, payload.content, captured_at.isoformat()
    )
    if length >= streams.MEMORY_STREAM_MAXLEN:
        await run_in_threadpool(flush_clone_stream, profile.id)
    return MemoryWriteResponse()


@router.get("/stream/status")
async def stream_status(profile: OwnProfile) -> dict[str, int]:
    return {"buffered_entries": await streams.stream_length(profile.id)}


@router.get("/backups", response_model=list[BackupResponse])
async def list_own_backups(
    profile: OwnProfile,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryBackup]:
    result = await db.execute(
        select(MemoryBackup)
        .where(MemoryBackup.clone_id == profile.id)
        .order_by(MemoryBackup.period_end.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _detail_response(backup: MemoryBackup) -> BackupDetailResponse:
    raw = await object_storage.get_object(backup.storage_key)
    return BackupDetailResponse(
        id=backup.id,
        clone_id=backup.clone_id,
        period_start=backup.period_start,
        period_end=backup.period_end,
        entry_count=backup.entry_count,
        created_at=backup.created_at,
        restored_at=backup.restored_at,
        payload=backup_codec.decode_payload(raw),
    )


@router.get("/backups/{backup_id}", response_model=BackupDetailResponse)
async def read_own_backup(backup: OwnBackup) -> BackupDetailResponse:
    return await _detail_response(backup)


@router.post("/backups/{backup_id}/restore", response_model=BackupDetailResponse)
async def restore_own_backup(
    backup: OwnBackup, db: DatabaseSession
) -> BackupDetailResponse:
    backup.restored_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(backup)
    return await _detail_response(backup)


@router.post(
    "/resurrect",
    response_model=BackupDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def resurrect_from(
    payload: ResurrectRequest,
    profile: OwnProfile,
    db: DatabaseSession,
) -> BackupDetailResponse:
    if payload.source_profile_id == profile.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resurrect from your own profile",
        )

    source = await db.get(CloneProfile, payload.source_profile_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source clone not found",
        )

    if source.status == ACTIVE_STATUS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot resurrect from a clone that is still active",
        )

    latest = await db.scalar(
        select(MemoryBackup)
        .where(MemoryBackup.clone_id == source.id)
        .order_by(MemoryBackup.period_end.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source clone has no backup to resurrect from",
        )

    raw = await object_storage.get_object(latest.storage_key)
    new_key = object_storage.backup_key(
        profile.id, latest.period_start, latest.period_end
    )
    await object_storage.put_object(new_key, raw)

    resurrected = MemoryBackup(
        clone_id=profile.id,
        period_start=latest.period_start,
        period_end=latest.period_end,
        entry_count=latest.entry_count,
        storage_key=new_key,
        subscription_tier=profile.subscription_tier,
        restored_at=datetime.now(UTC),
    )
    db.add(resurrected)
    await db.commit()
    await db.refresh(resurrected)
    return await _detail_response(resurrected)
