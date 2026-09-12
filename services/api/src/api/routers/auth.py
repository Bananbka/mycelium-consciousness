from __future__ import annotations

from functools import cache

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import CurrentUser, DatabaseSession
from api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from api.security import create_access_token, hash_password, verify_password
from shared.db.models import CloneProfile, User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


@cache
def _dummy_hash() -> str:
    """A real argon2 hash to verify against when the email is unknown.

    Without it, a miss returns after one indexed SELECT while a hit pays for a
    full argon2 verification, and the latency gap reveals which addresses are
    registered.
    """
    return hash_password("no-such-user-constant-time-padding")


INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterRequest, db: DatabaseSession) -> User:
    """Self-registration, always as a CLONE.

    Role is never taken from the request body; privilege escalation by posting
    `role: admin` is impossible by construction. Admins are seeded out of band.
    """
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role=UserRole.CLONE,
    )
    user.profile = CloneProfile(designation=payload.designation)
    db.add(user)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or designation already registered",
        ) from None

    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DatabaseSession) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()

    # Same error *and* the same amount of work for an unknown email as for a
    # wrong password, so neither the body nor the timing enumerates accounts.
    if user is None:
        verify_password(payload.password, _dummy_hash())
        raise INVALID_CREDENTIALS

    if not verify_password(payload.password, user.password_hash):
        raise INVALID_CREDENTIALS

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    return TokenResponse(access_token=create_access_token(user.id, user.role.value))


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: CurrentUser) -> User:
    return user
