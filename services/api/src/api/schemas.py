from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.roles import UserRole
from shared.subscriptions import SubscriptionTier


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    designation: str = Field(min_length=3, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    designation: str
    status: str
    subscription_tier: SubscriptionTier
    user_id: int | None
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    designation: str | None = Field(default=None, min_length=3, max_length=64)
    status: str | None = Field(default=None, min_length=1, max_length=32)


class SubscriptionUpdateRequest(BaseModel):
    subscription_tier: SubscriptionTier


class MemoryWriteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8192)
    captured_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped

    @field_validator("captured_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("captured_at must include a UTC offset")
        return value


class MemoryWriteResponse(BaseModel):
    status: str = "recorded"


class BackupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clone_id: int
    period_start: datetime
    period_end: datetime
    entry_count: int
    created_at: datetime
    restored_at: datetime | None


class BackupDetailResponse(BackupResponse):
    payload: list[dict]


class ResurrectRequest(BaseModel):
    source_profile_id: int


class RollupTriggerResponse(BaseModel):
    clone_id: int
    status: str
    entry_count: int


class HomeResponse(BaseModel):
    message: str
    role: UserRole
    email: EmailStr
