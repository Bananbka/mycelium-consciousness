"""MVP business logic: memory capture and semantic recall."""

from __future__ import annotations


async def test_created_memory_is_returned_in_the_listing(client, clone_headers):
    created = await client.post(
        "/memories",
        headers=clone_headers,
        json={"content": "the airlock code is seven four two"},
    )
    assert created.status_code == 201

    listing = await client.get("/memories", headers=clone_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["content"] == "the airlock code is seven four two"


async def test_search_ranks_the_matching_memory_first(client, clone_headers):
    memories = [
        "the airlock code is seven four two",
        "hydroponics bay needs more nitrogen",
        "captain vela prefers black coffee",
    ]
    for content in memories:
        response = await client.post(
            "/memories", headers=clone_headers, json={"content": content}
        )
        assert response.status_code == 201

    response = await client.get(
        "/memories/search",
        headers=clone_headers,
        params={"q": "hydroponics bay nitrogen"},
    )

    assert response.status_code == 200
    results = response.json()
    assert results
    assert results[0]["content"] == "hydroponics bay needs more nitrogen"
    assert results[0]["similarity"] > 0


async def test_search_respects_the_limit(client, clone_headers):
    for index in range(5):
        await client.post(
            "/memories",
            headers=clone_headers,
            json={"content": f"memory number {index}"},
        )

    response = await client.get(
        "/memories/search",
        headers=clone_headers,
        params={"q": "memory", "limit": 2},
    )

    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_search_returns_empty_when_the_clone_has_no_memories(
    client, clone_headers
):
    response = await client.get(
        "/memories/search",
        headers=clone_headers,
        params={"q": "anything at all"},
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_empty_content_is_rejected(client, clone_headers):
    response = await client.post(
        "/memories", headers=clone_headers, json={"content": ""}
    )
    assert response.status_code == 422


async def test_admin_stats_count_the_registry(
    client, admin_headers, clone_headers, clone_user
):
    await client.post(
        "/memories", headers=clone_headers, json={"content": "a stored memory"}
    )

    response = await client.get("/admin/stats", headers=admin_headers)
    assert response.status_code == 200

    stats = response.json()
    assert stats["users"] >= 2
    assert stats["clones"] >= 1
    assert stats["memories"] >= 1


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


async def test_blank_content_is_rejected(client, clone_headers):
    """A zero vector makes cosine distance NaN, which is invalid JSON."""
    response = await client.post(
        "/memories", headers=clone_headers, json={"content": "   "}
    )
    assert response.status_code == 422


async def test_content_is_stored_stripped(client, clone_headers):
    response = await client.post(
        "/memories", headers=clone_headers, json={"content": "  padded memory  "}
    )
    assert response.status_code == 201
    assert response.json()["content"] == "padded memory"


async def test_search_never_returns_nan_similarity(client, clone_headers):
    await client.post(
        "/memories", headers=clone_headers, json={"content": "a real memory"}
    )
    response = await client.get(
        "/memories/search", headers=clone_headers, params={"q": "real memory"}
    )
    assert response.status_code == 200
    for item in response.json():
        assert item["similarity"] == item["similarity"], "similarity is NaN"


async def test_listing_is_stable_across_identical_timestamps(client, clone_headers):
    """A whole micro-batch shares one timestamp; id must break the tie."""
    for i in range(10):
        await client.post(
            "/memories", headers=clone_headers, json={"content": f"memory {i}"}
        )

    first = await client.get("/memories", headers=clone_headers, params={"limit": 5})
    second = await client.get("/memories", headers=clone_headers, params={"limit": 5})

    assert [m["id"] for m in first.json()] == [m["id"] for m in second.json()]
