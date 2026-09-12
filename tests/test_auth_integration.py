"""Registration and login: success paths and failure modes."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from shared.db.models import CloneProfile, User, UserRole


async def test_register_creates_clone_with_linked_profile(client, session):
    response = await client.post(
        "/auth/register",
        json={
            "email": "new@clones.example.com",
            "password": "newpass12345",
            "designation": "clone-gamma",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@clones.example.com"
    assert body["role"] == "clone"
    assert body["is_active"] is True
    assert "password" not in body and "password_hash" not in body

    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.user_id == body["id"])
    )
    assert profile is not None
    assert profile.designation == "clone-gamma"


async def test_register_stores_a_hash_not_the_password(client, session):
    await client.post(
        "/auth/register",
        json={
            "email": "hash@clones.example.com",
            "password": "plaintext1234",
            "designation": "clone-hash",
        },
    )

    user = await session.scalar(
        select(User).where(User.email == "hash@clones.example.com")
    )
    assert user is not None
    assert user.password_hash != "plaintext1234"
    assert user.password_hash.startswith("$argon2")


async def test_register_cannot_self_assign_admin_role(client, session):
    """Role is ignored if supplied; escalation via the body must be impossible."""
    response = await client.post(
        "/auth/register",
        json={
            "email": "sneaky@clones.example.com",
            "password": "sneakypass123",
            "designation": "clone-sneaky",
            "role": "admin",
            "is_active": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["role"] == "clone"

    user = await session.scalar(
        select(User).where(User.email == "sneaky@clones.example.com")
    )
    assert user.role is UserRole.CLONE


async def test_register_rejects_duplicate_email(client, clone_user):
    response = await client.post(
        "/auth/register",
        json={
            "email": "alpha@clones.example.com",
            "password": "anotherpass123",
            "designation": "clone-duplicate",
        },
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"email": "bad", "password": "longenough1", "designation": "abc"}, "email"),
        ({"email": "a@b.co", "password": "short", "designation": "abc"}, "password"),
        ({"email": "a@b.co", "password": "longenough1", "designation": "x"}, "slug"),
    ],
)
async def test_register_validates_input(client, payload, reason):
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 422, reason


async def test_login_with_valid_credentials_returns_200_and_token(client, clone_user):
    response = await client.post(
        "/auth/login",
        json={"email": "alpha@clones.example.com", "password": "alphapass123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_with_wrong_password_is_401(client, clone_user):
    response = await client.post(
        "/auth/login",
        json={"email": "alpha@clones.example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


async def test_login_with_unknown_email_is_401(client):
    response = await client.post(
        "/auth/login",
        json={"email": "ghost@clones.example.com", "password": "whatever12345"},
    )
    assert response.status_code == 401


async def test_login_does_not_leak_whether_the_account_exists(client, clone_user):
    """Unknown email and wrong password must be indistinguishable."""
    unknown = await client.post(
        "/auth/login",
        json={"email": "ghost@clones.example.com", "password": "alphapass123"},
    )
    wrong = await client.post(
        "/auth/login",
        json={"email": "alpha@clones.example.com", "password": "wrong-password"},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


async def test_deactivated_account_cannot_log_in(client, session, clone_user):
    clone_user.is_active = False
    await session.flush()

    response = await client.post(
        "/auth/login",
        json={"email": "alpha@clones.example.com", "password": "alphapass123"},
    )
    assert response.status_code == 403


async def test_token_is_rejected_once_the_account_is_deactivated(
    client, session, clone_user, clone_headers
):
    assert (await client.get("/auth/me", headers=clone_headers)).status_code == 200

    clone_user.is_active = False
    await session.flush()

    assert (await client.get("/auth/me", headers=clone_headers)).status_code == 401
