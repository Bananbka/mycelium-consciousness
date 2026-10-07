from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from api.security import decode_access_token
from shared import cache
from shared.db.db import get_db
from shared.db.models import CloneProfile, MemoryBackup, User, UserRole
from shared.settings import settings
from shared.subscriptions import SubscriptionTier

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so a missing header reaches our handler and returns 401 with
# a WWW-Authenticate challenge, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _principal_snapshot(user: User) -> dict:
    """The fields requests need from a user and its profile; never the hash."""
    profile = user.profile
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role.value,
        "created_at": user.created_at.isoformat(),
        "profile": None
        if profile is None
        else {
            "id": profile.id,
            "designation": profile.designation,
            "status": profile.status,
            "subscription_tier": profile.subscription_tier.value,
            "user_id": profile.user_id,
            "created_at": profile.created_at.isoformat(),
        },
    }


def _principal_from_snapshot(data: dict) -> User:
    """Rebuild transient (session-less) ORM objects from a cached snapshot.

    They are read-only stand-ins: handlers that modify a profile must re-load
    it from the database (see `profiles.update_own_profile`).
    """
    user = User(
        id=data["id"],
        email=data["email"],
        password_hash="",
        role=UserRole(data["role"]),
        is_active=True,
        created_at=datetime.fromisoformat(data["created_at"]),
    )
    if (p := data["profile"]) is not None:
        user.profile = CloneProfile(
            id=p["id"],
            designation=p["designation"],
            status=p["status"],
            subscription_tier=SubscriptionTier(p["subscription_tier"]),
            user_id=p["user_id"],
            created_at=datetime.fromisoformat(p["created_at"]),
        )
    return user


async def get_current_user(
    db: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise UNAUTHORIZED

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise UNAUTHORIZED

    subject = payload.get("sub")
    if subject is None:
        raise UNAUTHORIZED

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise UNAUTHORIZED from None

    ttl = settings.auth_cache_ttl_seconds
    if ttl:
        snapshot = await cache.get_json(cache.principal_key(user_id))
        if snapshot is not None:
            return _principal_from_snapshot(snapshot)

    # One LEFT JOIN round trip (was SELECT users + a second SELECT for profile).
    result = await db.execute(
        select(User).options(joinedload(User.profile)).where(User.id == user_id)
    )
    user = result.unique().scalar_one_or_none()

    if user is None or not user.is_active:
        raise UNAUTHORIZED

    if ttl:
        await cache.set_json(
            cache.principal_key(user_id), _principal_snapshot(user), ttl
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole):
    """Vertical access control: authenticated but wrong role is 403, not 401."""

    async def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges",
            )
        return user

    return dependency


CurrentAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
CurrentClone = Annotated[User, Depends(require_role(UserRole.CLONE))]


async def get_own_profile(user: CurrentClone) -> CloneProfile:
    if user.profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No clone profile linked to this account",
        )

    return user.profile


OwnProfile = Annotated[CloneProfile, Depends(get_own_profile)]


async def get_own_backup(
    backup_id: int,
    profile: OwnProfile,
    db: DatabaseSession,
) -> MemoryBackup:
    backup = await db.get(MemoryBackup, backup_id)
    if backup is None or backup.clone_id != profile.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    return backup


OwnBackup = Annotated[MemoryBackup, Depends(get_own_backup)]
