from __future__ import annotations

from shared.auth import (
    ACCESS_TOKEN_TTL_MINUTES,
    DEV_SECRET,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    KNOWN_INSECURE_SECRETS,
    MIN_SECRET_LENGTH,
    InsecureSecretError,
    validate_jwt_secret,
)

__all__ = [
    "ACCESS_TOKEN_TTL_MINUTES",
    "DEV_SECRET",
    "InsecureSecretError",
    "JWT_ALGORITHM",
    "JWT_SECRET_KEY",
    "KNOWN_INSECURE_SECRETS",
    "MIN_SECRET_LENGTH",
    "validate_jwt_secret",
]
