from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEV_SECRET = "dev-only-insecure-secret-change-me"
MIN_SECRET_LENGTH = 32

# Values that are public by construction: the built-in default, whatever
# .env.example ships, and the usual throwaways.
KNOWN_INSECURE_SECRETS = frozenset(
    {
        DEV_SECRET,
        "replace-me-with-at-least-32-random-bytes",
        "change-me",
        "changeme",
        "secret",
        "password",
        "test",
    }
)

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", DEV_SECRET)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_TTL_MINUTES = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "60"))

MEMORY_SEARCH_DEFAULT_LIMIT = int(os.getenv("MEMORY_SEARCH_DEFAULT_LIMIT", "10"))
MEMORY_SEARCH_MAX_LIMIT = int(os.getenv("MEMORY_SEARCH_MAX_LIMIT", "50"))


class InsecureSecretError(RuntimeError):
    """The configured JWT secret would let anyone mint admin tokens."""


def validate_jwt_secret(secret: str | None = None) -> None:
    """Refuse to serve with a guessable signing key.

    A warning is not enough here: anyone holding a published secret can forge a
    token for any user id, so this fails startup instead. Set
    ALLOW_INSECURE_JWT_SECRET=1 to override for throwaway local runs.
    """
    secret = JWT_SECRET_KEY if secret is None else secret

    if os.getenv("ALLOW_INSECURE_JWT_SECRET") == "1":
        logger.warning(
            "ALLOW_INSECURE_JWT_SECRET is set; serving with an unvalidated "
            "JWT signing key. Never do this outside local development."
        )
        return

    if secret in KNOWN_INSECURE_SECRETS:
        raise InsecureSecretError(
            "JWT_SECRET_KEY is unset or set to a publicly known placeholder. "
            "Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )

    if len(secret) < MIN_SECRET_LENGTH:
        raise InsecureSecretError(
            f"JWT_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters; "
            f"got {len(secret)}."
        )
