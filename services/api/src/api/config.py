from __future__ import annotations

import os

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
    "MEMORY_SEARCH_DEFAULT_LIMIT",
    "MEMORY_SEARCH_MAX_LIMIT",
    "MIN_SECRET_LENGTH",
    "validate_jwt_secret",
]

MEMORY_SEARCH_DEFAULT_LIMIT = int(os.getenv("MEMORY_SEARCH_DEFAULT_LIMIT", "10"))
MEMORY_SEARCH_MAX_LIMIT = int(os.getenv("MEMORY_SEARCH_MAX_LIMIT", "50"))
