"""Horizontal access control: one clone must not reach another clone's data."""

from __future__ import annotations

from sqlalchemy import select

from shared.db.models import CloneProfile, MemoryChunk


async def _profile_id(session, designation: str) -> int:
    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.designation == designation)
    )
    assert profile is not None
    return profile.id


async def test_clone_cannot_read_another_clones_profile(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    response = await client.get(f"/profiles/{victim_id}", headers=clone_headers)
    assert response.status_code == 403


async def test_clone_cannot_patch_another_clones_profile(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    response = await client.patch(
        f"/profiles/{victim_id}",
        headers=clone_headers,
        json={"designation": "pwned", "status": "compromised"},
    )
    assert response.status_code == 403


async def test_a_rejected_idor_patch_does_not_mutate_the_victim(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")

    await client.patch(
        f"/profiles/{victim_id}",
        headers=clone_headers,
        json={"designation": "pwned", "status": "compromised"},
    )

    victim = await session.get(CloneProfile, victim_id)
    await session.refresh(victim)
    assert victim.designation == "clone-beta"
    assert victim.status == "active"


async def test_clone_can_patch_its_own_profile(client, session, clone_headers):
    own_id = await _profile_id(session, "clone-alpha")

    response = await client.patch(
        f"/profiles/{own_id}",
        headers=clone_headers,
        json={"status": "dormant"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dormant"


async def test_admin_may_patch_any_profile(client, session, admin_headers, clone_user):
    target_id = await _profile_id(session, "clone-alpha")

    response = await client.patch(
        f"/profiles/{target_id}",
        headers=admin_headers,
        json={"status": "quarantined"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"


async def test_profile_listing_is_scoped_to_the_caller(
    client, clone_headers, other_clone_user
):
    response = await client.get("/profiles", headers=clone_headers)
    assert response.status_code == 200

    designations = {item["designation"] for item in response.json()}
    assert designations == {"clone-alpha"}


async def test_admin_listing_sees_every_profile(
    client, admin_headers, clone_user, other_clone_user
):
    response = await client.get("/profiles", headers=admin_headers)
    assert response.status_code == 200

    designations = {item["designation"] for item in response.json()}
    assert {"clone-alpha", "clone-beta"} <= designations


async def test_clone_cannot_read_another_clones_memory(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")
    memory = MemoryChunk(clone_id=victim_id, content="beta private memory")
    session.add(memory)
    await session.flush()

    response = await client.get(f"/memories/{memory.id}", headers=clone_headers)
    assert response.status_code == 403


async def test_memory_written_by_a_clone_is_owned_by_that_clone(
    client, session, clone_headers
):
    """clone_id comes from the token, so it cannot be forged in the body."""
    victim_id = await _profile_id(session, "clone-alpha")

    response = await client.post(
        "/memories",
        headers=clone_headers,
        json={"content": "my own memory", "clone_id": 9999},
    )

    assert response.status_code == 201
    assert response.json()["clone_id"] == victim_id


async def test_memory_listing_never_includes_another_clones_rows(
    client, session, clone_headers, other_clone_user
):
    victim_id = await _profile_id(session, "clone-beta")
    session.add(MemoryChunk(clone_id=victim_id, content="beta secret"))
    await session.flush()

    await client.post(
        "/memories",
        headers=clone_headers,
        json={"content": "alpha memory"},
    )

    response = await client.get("/memories", headers=clone_headers)
    assert response.status_code == 200

    contents = {item["content"] for item in response.json()}
    assert contents == {"alpha memory"}
    assert "beta secret" not in contents
