from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.security import decode_access_token
from shared.db.db import get_db
from shared.db.models import CloneProfile, MemoryBackup, User, UserRole

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False so a missing header reaches our handler and returns 401 with
# a WWW-Authenticate challenge, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Not authenticated",
                        headers={"WWW-Authenticate": "Bearer"},
)


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

    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise UNAUTHORIZED

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
