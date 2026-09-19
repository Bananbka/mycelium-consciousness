"""Draining Redis streams into the memory_buffers row: the once-a-minute
flush and the MAXLEN safety valve that fires an immediate one-clone flush."""

from __future__ import annotations

from sqlalchemy import func, select

from celery_worker.flush import flush_all_streams, flush_one_stream
from shared import streams
from shared.db.models import MemoryBuffer


async def _entry_count(session, profile_id: int) -> int:
    buffer = await session.get(MemoryBuffer, profile_id)
    return buffer.entry_count if buffer is not None else 0


async def test_flush_one_stream_moves_entries_into_the_buffer(session, clone_user):
    profile_id = clone_user.profile.id
    for i in range(3):
        await streams.append_memory(
            profile_id, f"noise {i}", "2026-01-01T00:00:00+00:00"
        )

    result = await flush_one_stream(session, profile_id)
    assert result == {"clone_id": profile_id, "flushed": 3}

    assert await _entry_count(session, profile_id) == 3
    assert await streams.stream_length(profile_id) == 0

    buffer = await session.get(MemoryBuffer, profile_id)
    assert {item["content"] for item in buffer.payload} == {
        f"noise {i}" for i in range(3)
    }


async def test_flush_appends_to_an_existing_buffer_row_instead_of_a_new_one(
    session, clone_user
):
    profile_id = clone_user.profile.id
    await streams.append_memory(profile_id, "first", "2026-01-01T00:00:00+00:00")
    await flush_one_stream(session, profile_id)

    await streams.append_memory(profile_id, "second", "2026-01-01T00:01:00+00:00")
    await flush_one_stream(session, profile_id)

    buffer = await session.get(MemoryBuffer, profile_id)
    assert buffer.entry_count == 2
    assert [item["content"] for item in buffer.payload] == ["first", "second"]

    row_count = await session.scalar(
        select(func.count())
        .select_from(MemoryBuffer)
        .where(MemoryBuffer.clone_id == profile_id)
    )
    assert row_count == 1


async def test_flush_one_stream_of_an_empty_clone_is_a_noop(session, clone_user):
    profile_id = clone_user.profile.id
    result = await flush_one_stream(session, profile_id)
    assert result == {"clone_id": profile_id, "flushed": 0}


async def test_flush_all_streams_only_touches_clones_with_data(
    session, clone_user, other_clone_user
):
    await streams.append_memory(
        clone_user.profile.id, "alpha noise", "2026-01-01T00:00:00+00:00"
    )

    results = await flush_all_streams(session)

    processed_ids = {r["clone_id"] for r in results}
    assert clone_user.profile.id in processed_ids
    assert other_clone_user.profile.id not in processed_ids
    assert await _entry_count(session, clone_user.profile.id) == 1


async def test_write_past_maxlen_triggers_an_immediate_flush(
    client, session, clone_headers, clone_user, monkeypatch
):
    monkeypatch.setattr(streams, "MEMORY_STREAM_MAXLEN", 3)

    flushed_ids: list[int] = []
    monkeypatch.setattr("api.routers.memories.flush_clone_stream", flushed_ids.append)

    for i in range(3):
        response = await client.post(
            "/memories/write", headers=clone_headers, json={"content": f"noise {i}"}
        )
        assert response.status_code == 202

    assert flushed_ids == [clone_user.profile.id]


async def test_write_below_maxlen_does_not_trigger_a_flush(
    client, clone_headers, monkeypatch
):
    monkeypatch.setattr(streams, "MEMORY_STREAM_MAXLEN", 100)

    flushed_ids: list[int] = []
    monkeypatch.setattr("api.routers.memories.flush_clone_stream", flushed_ids.append)

    response = await client.post(
        "/memories/write", headers=clone_headers, json={"content": "just one"}
    )
    assert response.status_code == 202
    assert flushed_ids == []
