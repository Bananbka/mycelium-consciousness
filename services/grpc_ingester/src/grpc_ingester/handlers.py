from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

DEFAULT_BATCH_FRAMES = int(os.getenv("INGEST_BATCH_FRAMES", "64"))
DEFAULT_BATCH_BYTES = int(os.getenv("INGEST_BATCH_BYTES", str(1 << 20)))


@dataclass(slots=True)
class IngestionStats:
    clone_id: str = "unknown"
    frames_received: int = 0
    bytes_received: int = 0


@dataclass(slots=True)
class MicroBatch:
    clone_id: str
    frames: int
    payload_size: int
    captured_at_unix_ms: list[int] = field(default_factory=list)


async def iter_micro_batches(
    request_iterator: AsyncIterator,
    stats: IngestionStats,
    *,
    max_frames: int = DEFAULT_BATCH_FRAMES,
    max_bytes: int = DEFAULT_BATCH_BYTES,
) -> AsyncIterator[MicroBatch]:
    frames = 0
    payload_size = 0
    timestamps: list[int] = []

    async for frame in request_iterator:
        clone_id = getattr(frame, "clone_id", "") or stats.clone_id
        stats.clone_id = clone_id
        payload = getattr(frame, "payload", b"") or b""

        stats.frames_received += 1
        stats.bytes_received += len(payload)
        frames += 1
        payload_size += len(payload)
        timestamps.append(getattr(frame, "captured_at_unix_ms", 0))

        if frames >= max_frames or payload_size >= max_bytes:
            yield MicroBatch(clone_id, frames, payload_size, timestamps)
            frames = 0
            payload_size = 0
            timestamps = []

    if frames:
        yield MicroBatch(stats.clone_id, frames, payload_size, timestamps)


async def collect_stream(request_iterator: AsyncIterator) -> IngestionStats:
    stats = IngestionStats()
    async for _ in iter_micro_batches(request_iterator, stats):
        pass
    return stats
