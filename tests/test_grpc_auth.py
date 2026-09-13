"""Unit tests for the gRPC bearer-token check (no server, no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from grpc_ingester.auth import (
    GrpcAuthenticationError,
    GrpcAuthorizationError,
    authenticate,
)
from shared.auth import JWT_ALGORITHM, JWT_SECRET_KEY, create_access_token


class _FakeContext:
    def __init__(self, metadata: tuple[tuple[str, str], ...] = ()) -> None:
        self._metadata = metadata

    def invocation_metadata(self):
        return self._metadata


def _bearer(token: str) -> _FakeContext:
    return _FakeContext((("authorization", f"Bearer {token}"),))


def test_valid_clone_token_resolves_the_user_id():
    token = create_access_token(user_id=42, role="clone")
    assert authenticate(_bearer(token)) == 42


def test_missing_metadata_is_rejected():
    with pytest.raises(GrpcAuthenticationError):
        authenticate(_FakeContext())


def test_missing_bearer_prefix_is_rejected():
    context = _FakeContext((("authorization", "not-a-bearer-token"),))
    with pytest.raises(GrpcAuthenticationError):
        authenticate(context)


def test_malformed_token_is_rejected():
    with pytest.raises(GrpcAuthenticationError):
        authenticate(_bearer("garbage"))


def test_expired_token_is_rejected():
    past = datetime.now(UTC) - timedelta(minutes=5)
    expired = jwt.encode(
        {"sub": "1", "role": "clone", "exp": int(past.timestamp())},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(GrpcAuthenticationError):
        authenticate(_bearer(expired))


def test_admin_token_is_rejected_as_authorization_not_authentication():
    token = create_access_token(user_id=1, role="admin")
    with pytest.raises(GrpcAuthorizationError):
        authenticate(_bearer(token))


def test_token_missing_subject_is_rejected():
    tokenless = jwt.encode(
        {
            "role": "clone",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(GrpcAuthenticationError):
        authenticate(_bearer(tokenless))
