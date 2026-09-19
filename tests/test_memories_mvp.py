"""MVP business logic: the fast write path and admin registry counts."""

from __future__ import annotations

from shared import streams


async def test_write_is_accepted_and_lands_in_the_stream(
    client, clone_headers, clone_user
):
    response = await client.post(
        "/memories/write",
        headers=clone_headers,
        json={"content": "the airlock code is seven four two"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "recorded"

    profile_id = clone_user.profile.id
    entries = await streams.read_all(profile_id)
    assert len(entries) == 1
    assert entries[0][1]["content"] == "the airlock code is seven four two"


async def test_stream_status_reports_the_buffered_count(
    client, clone_headers, clone_user
):
    for i in range(5):
        await client.post(
            "/memories/write", headers=clone_headers, json={"content": f"noise {i}"}
        )

    response = await client.get("/memories/stream/status", headers=clone_headers)
    assert response.status_code == 200
    assert response.json()["buffered_entries"] == 5


async def test_empty_content_is_rejected(client, clone_headers):
    response = await client.post(
        "/memories/write", headers=clone_headers, json={"content": ""}
    )
    assert response.status_code == 422


async def test_blank_content_is_rejected(client, clone_headers):
    response = await client.post(
        "/memories/write", headers=clone_headers, json={"content": "   "}
    )
    assert response.status_code == 422


async def test_write_uses_the_callers_own_profile_regardless_of_body(
    client, clone_headers, clone_user, other_clone_user
):
    response = await client.post(
        "/memories/write",
        headers=clone_headers,
        json={"content": "mine", "clone_id": other_clone_user.profile.id},
    )
    assert response.status_code == 202

    own_entries = await streams.read_all(clone_user.profile.id)
    other_entries = await streams.read_all(other_clone_user.profile.id)
    assert len(own_entries) == 1
    assert len(other_entries) == 0


async def test_admin_stats_count_the_registry(client, admin_headers, clone_user):
    response = await client.get("/admin/stats", headers=admin_headers)
    assert response.status_code == 200

    stats = response.json()
    assert stats["users"] >= 2
    assert stats["clones"] >= 1


async def test_admin_can_deactivate_a_clone(client, admin_headers, clone_user):
    response = await client.post(
        f"/admin/users/{clone_user.id}/deactivate", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


async def test_admin_cannot_deactivate_themselves(client, admin_headers, admin_user):
    response = await client.post(
        f"/admin/users/{admin_user.id}/deactivate", headers=admin_headers
    )
    assert response.status_code == 400


async def test_clone_cannot_deactivate_another_user(
    client, clone_headers, other_clone_user
):
    response = await client.post(
        f"/admin/users/{other_clone_user.id}/deactivate", headers=clone_headers
    )
    assert response.status_code == 403


async def test_admin_can_change_a_clones_subscription_tier(
    client, admin_headers, clone_user
):
    response = await client.patch(
        f"/admin/clones/{clone_user.profile.id}/subscription",
        headers=admin_headers,
        json={"subscription_tier": "premium"},
    )
    assert response.status_code == 200
    assert response.json()["subscription_tier"] == "premium"


async def test_clone_cannot_change_their_own_subscription_tier(
    client, clone_headers, clone_user
):
    response = await client.patch(
        f"/admin/clones/{clone_user.profile.id}/subscription",
        headers=clone_headers,
        json={"subscription_tier": "premium"},
    )
    assert response.status_code == 403


async def test_admin_can_force_a_rollup(client, admin_headers, clone_user, monkeypatch):
    def fake_force_rollup_clone(clone_id: int, timeout: float = 15.0) -> dict:
        assert clone_id == clone_user.profile.id
        return {"clone_id": clone_id, "status": "backed_up", "entry_count": 3}

    monkeypatch.setattr("api.routers.admin.force_rollup_clone", fake_force_rollup_clone)

    response = await client.post(
        f"/admin/clones/{clone_user.profile.id}/rollup", headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "clone_id": clone_user.profile.id,
        "status": "backed_up",
        "entry_count": 3,
    }


async def test_clone_cannot_trigger_a_rollup(client, clone_headers, clone_user):
    response = await client.post(
        f"/admin/clones/{clone_user.profile.id}/rollup", headers=clone_headers
    )
    assert response.status_code == 403


async def test_force_rollup_on_a_missing_clone_is_404(client, admin_headers):
    response = await client.post("/admin/clones/999999/rollup", headers=admin_headers)
    assert response.status_code == 404
