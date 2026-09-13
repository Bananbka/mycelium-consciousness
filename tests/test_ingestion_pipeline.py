"""The gRPC -> Celery ingestion path: batching and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from celery_worker.pipeline import store_batch
from grpc_ingester.handlers import (
    IngestionStats,
    MixedCloneStreamError,
    iter_micro_batches,
)
from shared.db.models import CloneProfile, MemoryChunk


@dataclass
class FakeFrame:
    clone_id: str
    captured_at_unix_ms: int
    payload: bytes


async def _frames(count: int, clone_id: str = "clone-alpha"):
    for i in range(count):
        yield FakeFrame(clone_id, 1_700_000_000_000 + i, f"frame {i}".encode())


async def test_batches_flush_at_the_frame_threshold():
    stats = IngestionStats()
    batches = [b async for b in iter_micro_batches(_frames(150), stats, max_frames=64)]

    assert [b.frames for b in batches] == [64, 64, 22]
    assert stats.frames_received == 150


async def test_batch_carries_the_raw_payload_bytes():
    """The payload must survive to the worker, not just its byte count."""
    stats = IngestionStats()
    batches = [b async for b in iter_micro_batches(_frames(3), stats)]

    assert batches[0].payloads == [b"frame 0", b"frame 1", b"frame 2"]
    assert batches[0].payload_size == sum(len(f"frame {i}") for i in range(3))


async def test_non_utf8_payload_passes_through_the_handler_unchanged():
    async def binary():
        yield FakeFrame("clone-alpha", 1, b"\xff\xfe invalid utf8")

    stats = IngestionStats()
    batches = [b async for b in iter_micro_batches(binary(), stats)]
    assert batches[0].payloads == [b"\xff\xfe invalid utf8"]


async def test_store_batch_persists_chunks_against_the_right_clone(session, clone_user):
    profile = await session.scalar(
        select(CloneProfile).where(CloneProfile.designation == "clone-alpha")
    )

    result = await store_batch(
        session,
        clone_user.id,
        [b"airlock code is seven four two", b"bay needs nitrogen"],
    )

    assert result["status"] == "stored"
    assert result["stored"] == 2

    rows = (
        await session.scalars(
            select(MemoryChunk).where(MemoryChunk.clone_id == profile.id)
        )
    ).all()
    assert {r.content for r in rows} == {
        "airlock code is seven four two",
        "bay needs nitrogen",
    }
    assert all(r.embedding is not None for r in rows)


async def test_store_batch_rejects_a_user_with_no_clone_profile(session, admin_user):
    """The token identifies a user; an admin has no profile to write into."""
    result = await store_batch(session, admin_user.id, [b"some memory"])

    assert result["status"] == "no_clone_profile"
    assert result["stored"] == 0

    count = await session.scalar(select(MemoryChunk))
    assert count is None


async def test_store_batch_rejects_an_unknown_user_id(session):
    result = await store_batch(session, 999_999_999, [b"some memory"])

    assert result["status"] == "no_clone_profile"
    assert result["stored"] == 0


async def test_store_batch_skips_blank_frames(session, clone_user):
    result = await store_batch(session, clone_user.id, [b"real memory", b"   ", b""])

    assert result["stored"] == 1


async def test_store_batch_drops_a_non_utf8_frame_instead_of_corrupting_it(
    session, clone_user
):
    result = await store_batch(
        session, clone_user.id, [b"\xff\xfe not valid utf8", b"a real memory"]
    )

    assert result["stored"] == 1

    rows = (await session.scalars(select(MemoryChunk))).all()
    assert [r.content for r in rows] == ["a real memory"]


@pytest.mark.parametrize("payloads", [[], None])
async def test_store_batch_handles_an_empty_batch(session, clone_user, payloads):
    result = await store_batch(session, clone_user.id, payloads or [])
    assert result["stored"] == 0


async def test_a_stream_may_omit_clone_id_after_the_first_frame():
    async def mixed():
        yield FakeFrame("clone-alpha", 1, b"first")
        yield FakeFrame("", 2, b"second")

    stats = IngestionStats()
    batches = [b async for b in iter_micro_batches(mixed(), stats)]

    assert batches[0].clone_id == "clone-alpha"
    assert batches[0].payloads == [b"first", b"second"]


async def test_changing_clone_id_mid_stream_is_rejected():
    """Otherwise every frame in the batch is attributed to the last clone seen."""

    async def mixed():
        yield FakeFrame("clone-alpha", 1, b"mine")
        yield FakeFrame("clone-beta", 2, b"theirs")

    stats = IngestionStats()
    with pytest.raises(MixedCloneStreamError) as excinfo:
        [b async for b in iter_micro_batches(mixed(), stats)]

    assert excinfo.value.expected == "clone-alpha"
    assert excinfo.value.received == "clone-beta"


async def test_stream_pins_to_the_first_frame_that_names_a_clone():
    """proto3 sends an unset clone_id as "", which must not pin the stream."""

    async def frames():
        yield FakeFrame("", 1, b"first")
        yield FakeFrame("clone-alpha", 2, b"second")
        yield FakeFrame("", 3, b"third")

    stats = IngestionStats()
    batches = [b async for b in iter_micro_batches(frames(), stats)]

    assert batches[0].clone_id == "clone-alpha"
    assert batches[0].payloads == [b"first", b"second", b"third"]
    assert stats.clone_id == "clone-alpha"


async def test_capture_timestamps_reach_the_stored_rows(session, clone_user):
    captured = [1_700_000_000_000, 1_700_000_060_000]

    result = await store_batch(
        session, clone_user.id, [b"first memory", b"second memory"], captured
    )
    assert result["stored"] == 2

    rows = (
        await session.scalars(
            select(MemoryChunk)
            .order_by(MemoryChunk.id)
            .where(MemoryChunk.content.in_(["first memory", "second memory"]))
        )
    ).all()
    stored = {r.content: r.timestamp for r in rows}

    assert stored["first memory"] == datetime.fromtimestamp(captured[0] / 1000, tz=UTC)
    assert stored["second memory"] == datetime.fromtimestamp(captured[1] / 1000, tz=UTC)


async def test_missing_capture_timestamp_falls_back_to_the_server_default(
    session, clone_user
):
    result = await store_batch(session, clone_user.id, [b"no timestamp"], [0])
    assert result["stored"] == 1

    row = await session.scalar(
        select(MemoryChunk).where(MemoryChunk.content == "no timestamp")
    )
    assert row.timestamp is not None


async def test_blank_frames_do_not_desynchronise_timestamps(session, clone_user):
    """The blank frame is skipped, but the survivor keeps its own timestamp."""
    captured = [1_700_000_000_000, 1_700_000_060_000]

    await store_batch(session, clone_user.id, [b"   ", b"kept"], captured)

    row = await session.scalar(select(MemoryChunk).where(MemoryChunk.content == "kept"))
    assert row.timestamp == datetime.fromtimestamp(captured[1] / 1000, tz=UTC)
