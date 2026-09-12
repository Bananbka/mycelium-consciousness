"""The JWT signing key must not be guessable."""

from __future__ import annotations

import pytest

from api.config import (
    DEV_SECRET,
    KNOWN_INSECURE_SECRETS,
    InsecureSecretError,
    validate_jwt_secret,
)

# Low-entropy on purpose: a real high-entropy secret here trips
# gitleaks on every commit even though it is only a length fixture.
GOOD_SECRET = "this-is-a-fixture-secret-not-a-real-one-" * 2


def test_builtin_dev_secret_is_refused():
    with pytest.raises(InsecureSecretError):
        validate_jwt_secret(DEV_SECRET)


def test_the_env_example_placeholder_is_refused():
    """The value .env.example ships is public, so it must not boot."""
    with pytest.raises(InsecureSecretError):
        validate_jwt_secret("replace-me-with-at-least-32-random-bytes")


@pytest.mark.parametrize("secret", sorted(KNOWN_INSECURE_SECRETS))
def test_every_known_placeholder_is_refused(secret):
    with pytest.raises(InsecureSecretError):
        validate_jwt_secret(secret)


def test_short_secret_is_refused():
    with pytest.raises(InsecureSecretError, match="at least 32"):
        validate_jwt_secret("too-short")


def test_a_strong_secret_is_accepted():
    validate_jwt_secret(GOOD_SECRET)


def test_override_allows_an_insecure_secret(monkeypatch):
    monkeypatch.setenv("ALLOW_INSECURE_JWT_SECRET", "1")
    validate_jwt_secret(DEV_SECRET)
