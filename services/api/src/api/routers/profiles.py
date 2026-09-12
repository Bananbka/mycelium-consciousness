from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import CurrentUser, DatabaseSession, OwnProfile
from api.schemas import ProfileResponse, ProfileUpdateRequest
from shared.db.models import CloneProfile, User, UserRole

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _assert_can_access(profile: CloneProfile, user: User) -> None:
    """Horizontal access control.

    Ownership comes from the token subject, never from the path, so a clone
    reaching another clone's profile is rejected rather than served.
    """
    if user.role is UserRole.ADMIN:
        return
    if profile.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this profile",
        )


async def _apply_update(
    profile: CloneProfile,
    payload: ProfileUpdateRequest,
    db: DatabaseSession,
) -> CloneProfile:
    if payload.designation is not None:
        profile.designation = payload.designation
    if payload.status is not None:
        profile.status = payload.status

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Designation already taken",
        ) from None

    await db.refresh(profile)
    return profile


async def _load_accessible(
    profile_id: int,
    user: User,
    db: DatabaseSession,
) -> CloneProfile:
    profile = await db.get(CloneProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    _assert_can_access(profile, user)
    return profile


@router.get("/me", response_model=ProfileResponse)
async def read_own_profile(profile: OwnProfile) -> CloneProfile:
    return profile


@router.patch("/me", response_model=ProfileResponse)
async def update_own_profile(
    payload: ProfileUpdateRequest,
    profile: OwnProfile,
    db: DatabaseSession,
) -> CloneProfile:
    return await _apply_update(profile, payload, db)


@router.get("", response_model=list[ProfileResponse])
async def list_profiles(user: CurrentUser, db: DatabaseSession) -> list[CloneProfile]:
    query = select(CloneProfile).order_by(CloneProfile.id)
    if user.role is not UserRole.ADMIN:
        query = query.where(CloneProfile.user_id == user.id)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{profile_id}", response_model=ProfileResponse)
async def read_profile(
    profile_id: int,
    user: CurrentUser,
    db: DatabaseSession,
) -> CloneProfile:
    return await _load_accessible(profile_id, user, db)


@router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: int,
    payload: ProfileUpdateRequest,
    user: CurrentUser,
    db: DatabaseSession,
) -> CloneProfile:
    profile = await _load_accessible(profile_id, user, db)
    return await _apply_update(profile, payload, db)
