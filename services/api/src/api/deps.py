from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.security import decode_access_token
from shared import consistency
from shared.consistency import CONNECTIVITY_ERRORS, replica_breaker
from shared.db.db import REPLICA_ENABLED, ReplicaSessionLocal, get_db
from shared.db.models import CloneProfile, MemoryBackup, User, UserRole

logger = logging.getLogger(__name__)

# Every handler sees the primary through this: writes, auth, anything that must
# be strongly consistent. Reads that tolerate replica lag use ReadDatabaseSession.
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CONSISTENCY_HEADER = "X-Read-Consistency"  # strong | eventual | (absent = auto)

# auto_error=False so a missing header reaches our handler and returns 401 with
# a WWW-Authenticate challenge, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _load_user(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def _load_user_degraded(request: Request, user_id: int) -> User | None:
    """Primary unreachable: authenticate a safe request from the standby.

    The identity may lag the primary by the replication delay (a deactivation
    made a moment ago is not seen yet) - the accepted price of keeping reads
    available. Anything that mutates is refused outright.
    """
    unavailable = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Primary database unavailable; service is read-only",
        headers={"Retry-After": "5"},
    )
    if (
        not REPLICA_ENABLED
        or request.method not in SAFE_METHODS
        or replica_breaker.is_open
    ):
        raise unavailable
    try:
        async with ReplicaSessionLocal() as replica:
            user = await _load_user(replica, user_id)
    except CONNECTIVITY_ERRORS:
        replica_breaker.trip()
        raise unavailable from None
    request.state.degraded = True
    request.state.db_node = "replica-degraded"
    logger.warning("primary unavailable, serving %s from replica", request.url.path)
    return user


async def get_current_user(
    request: Request,
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

    try:
        user = await _load_user(db, user_id)
    except CONNECTIVITY_ERRORS:
        user = await _load_user_degraded(request, user_id)

    if user is None or not user.is_active:
        raise UNAUTHORIZED

    # Read by the read-after-write middleware and by the read router below.
    request.state.user_id = user.id
    if not hasattr(request.state, "db_node"):
        request.state.db_node = "primary"
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


async def _choose_read_node(request: Request, user: User) -> str:
    """Pick where a lag-tolerant read runs; the returned label is X-DB-Node."""
    if not REPLICA_ENABLED:
        return "primary"
    if getattr(request.state, "degraded", False):
        return "replica"

    preference = request.headers.get(CONSISTENCY_HEADER, "").lower()
    if preference == "strong":
        return "primary-strong"
    if replica_breaker.is_open:
        return "primary-fallback"
    if preference != "eventual":
        clone_id = user.profile.id if user.profile is not None else None
        if await consistency.recently_wrote(user.id, clone_id):
            return "primary-sticky"
    return "replica"


async def get_read_db(
    request: Request,
    user: CurrentUser,
    primary: DatabaseSession,
) -> AsyncIterator[AsyncSession]:
    """Session for reads that tolerate replica lag (eventual consistency).

    Falls back to the primary - never to an error - when the caller just wrote
    (read-after-write), asked for ``strong``, or the standby is unreachable.
    """
    node = await _choose_read_node(request, user)

    replica: AsyncSession | None = None
    if node == "replica":
        candidate = ReplicaSessionLocal()
        try:
            await candidate.connection()  # checks out (and pings) a connection now
        except CONNECTIVITY_ERRORS:
            await candidate.close()
            if getattr(request.state, "degraded", False):
                raise
            replica_breaker.trip()
            node = "primary-fallback"
            logger.warning("replica unreachable, reading from primary")
        else:
            replica_breaker.reset()
            replica = candidate

    if replica is None:
        request.state.db_node = node
        yield primary
        return

    # A request already authenticated off the standby keeps its degraded label.
    request.state.db_node = (
        "replica-degraded" if getattr(request.state, "degraded", False) else node
    )

    try:
        yield replica
    finally:
        await replica.close()


ReadDatabaseSession = Annotated[AsyncSession, Depends(get_read_db)]
