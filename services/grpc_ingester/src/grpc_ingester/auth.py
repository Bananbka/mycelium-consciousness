from __future__ import annotations

import grpc

from shared.auth import decode_access_token
from shared.roles import UserRole


class GrpcAuthenticationError(Exception):
    """Missing, malformed, or expired bearer token."""


class GrpcAuthorizationError(Exception):
    """A validly signed token that does not carry the required role."""


def authenticate(context: grpc.aio.ServicerContext) -> int:
    """Resolve the caller's user id from the call's bearer token."""
    metadata = dict(context.invocation_metadata() or ())
    header = metadata.get("authorization", "")

    if not header.startswith("Bearer "):
        raise GrpcAuthenticationError("missing bearer token")

    claims = decode_access_token(header.removeprefix("Bearer "))
    if claims is None:
        raise GrpcAuthenticationError("invalid or expired token")

    if claims.get("role") != UserRole.CLONE.value:
        raise GrpcAuthorizationError("token is not a clone token")

    try:
        return int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        raise GrpcAuthenticationError("token missing subject") from None
