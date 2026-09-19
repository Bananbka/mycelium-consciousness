"""Anonymous access (401) and vertical role separation (403)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from api.config import JWT_ALGORITHM, JWT_SECRET_KEY

PROTECTED_ROUTES = [
    ("GET", "/auth/me"),
    ("GET", "/me/home"),
    ("GET", "/admin/home"),
    ("GET", "/profiles/me"),
    ("PATCH", "/profiles/me"),
    ("GET", "/profiles"),
    ("POST", "/memories/write"),
    ("GET", "/memories/stream/status"),
    ("GET", "/memories/backups"),
    ("GET", "/memories/backups/1"),
    ("POST", "/memories/backups/1/restore"),
    ("GET", "/admin/users"),
    ("GET", "/admin/clones"),
    ("GET", "/admin/stats"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
async def test_anonymous_access_is_401(client, method, path):
    response = await client.request(method, path, json={})
    assert response.status_code == 401, f"{method} {path} allowed anonymous access"


@pytest.mark.parametrize(("method", "path"), PROTECTED_ROUTES)
async def test_anonymous_gets_a_bearer_challenge(client, method, path):
    response = await client.request(method, path, json={})
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer not-a-real-token",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "totally-malformed",
    ],
)
async def test_malformed_authorization_header_is_401(client, header):
    response = await client.get("/auth/me", headers={"Authorization": header})
    assert response.status_code == 401


ADMIN_ONLY_ROUTES = [
    ("GET", "/admin/home"),
    ("GET", "/admin/users"),
    ("GET", "/admin/clones"),
    ("GET", "/admin/stats"),
    ("GET", "/admin/clones/1/backups"),
]


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY_ROUTES)
async def test_clone_cannot_reach_admin_routes(client, clone_headers, method, path):
    """Vertical separation: authenticated but under-privileged is 403, not 401."""
    response = await client.request(method, path, headers=clone_headers)
    assert response.status_code == 403, f"{method} {path} leaked to a clone"


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY_ROUTES)
async def test_admin_can_reach_admin_routes(client, admin_headers, method, path):
    response = await client.request(method, path, headers=admin_headers)
    assert response.status_code == 200


async def test_clone_can_reach_its_own_home(client, clone_headers):
    response = await client.get("/me/home", headers=clone_headers)
    assert response.status_code == 200
    assert response.json()["role"] == "clone"


async def test_admin_cannot_reach_the_clone_console(client, admin_headers):
    """The separation runs both ways: an admin is not a clone."""
    response = await client.get("/me/home", headers=admin_headers)
    assert response.status_code == 403


async def test_admin_has_no_clone_profile(client, admin_headers):
    response = await client.get("/profiles/me", headers=admin_headers)
    assert response.status_code == 403


async def test_admin_cannot_write_to_the_memory_stream(client, admin_headers):
    response = await client.post(
        "/memories/write", headers=admin_headers, json={"content": "not a clone"}
    )
    assert response.status_code == 403


def _token_missing_subject() -> str:
    claims = {
        "role": "clone",
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
    }
    return jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def test_token_without_a_subject_is_rejected(client):
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {_token_missing_subject()}"},
    )
    assert response.status_code == 401
