from __future__ import annotations

from fastapi import APIRouter

from api.deps import CurrentAdmin, CurrentClone
from api.schemas import HomeResponse

router = APIRouter(tags=["home"])


@router.get("/me/home", response_model=HomeResponse)
async def clone_home(user: CurrentClone) -> HomeResponse:
    return HomeResponse(
        message="Clone console. Your memory stream is recording.",
        role=user.role,
        email=user.email,
    )


@router.get("/admin/home", response_model=HomeResponse)
async def admin_home(user: CurrentAdmin) -> HomeResponse:
    return HomeResponse(
        message="Operator console. Full clone registry access.",
        role=user.role,
        email=user.email,
    )
