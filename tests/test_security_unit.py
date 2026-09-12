"""Unit tests for the password and token primitives (no database, no app)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from api.config import JWT_ALGORITHM, JWT_SECRET_KEY
from api.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_is_not_the_plaintext():
    hashed = hash_password("hunter2hunter2")
    assert hashed != "hunter2hunter2"
    assert hashed.startswith("$argon2")


def test_hash_is_salted_per_call():
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_accepts_correct_password():
    assert verify_password("hunter2hunter2", hash_password("hunter2hunter2"))


def test_verify_rejects_wrong_password():
    assert not verify_password("wrong-password", hash_password("hunter2hunter2"))


def test_verify_rejects_malformed_hash():
    assert not verify_password("anything", "not-a-valid-hash")


def test_token_round_trips_subject_and_role():
    payload = decode_access_token(create_access_token(user_id=7, role="admin"))
    assert payload is not None
    assert payload["sub"] == "7"
    assert payload["role"] == "admin"


def test_expired_token_is_rejected():
    past = datetime.now(UTC) - timedelta(minutes=5)
    expired = jwt.encode(
        {"sub": "1", "role": "clone", "exp": int(past.timestamp())},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    assert decode_access_token(expired) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode(
        {
            "sub": "1",
            "role": "admin",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        "an-attacker-controlled-key-of-sufficient-length-0123456789",
        algorithm=JWT_ALGORITHM,
    )
    assert decode_access_token(forged) is None


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_malformed_tokens_are_rejected(token: str):
    assert decode_access_token(token) is None


def test_unsigned_alg_none_token_is_rejected():
    """A hand-forged alg=none token must never be accepted.

    Built by hand because PyJWT refuses to encode one.
    """

    def b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64({"alg": "none", "typ": "JWT"})
    claims = b64({"sub": "1", "role": "admin"})
    assert decode_access_token(f"{header}.{claims}.") is None
