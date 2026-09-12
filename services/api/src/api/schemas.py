from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.db.models import UserRole


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
    user_id: int | None
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    designation: str | None = Field(default=None, min_length=3, max_length=64)
    status: str | None = Field(default=None, min_length=1, max_length=32)


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8192)

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """Whitespace-only content embeds to a zero vector, whose cosine
        distance is NaN and serialises as invalid JSON."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    clone_id: int
    content: str
    timestamp: datetime


class MemorySearchResult(MemoryResponse):
    similarity: float


class HomeResponse(BaseModel):
    message: str
    role: UserRole
    email: EmailStr
