from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

DEFAULT_BATCH_FRAMES = int(os.getenv("INGEST_BATCH_FRAMES", "64"))
DEFAULT_BATCH_BYTES = int(os.getenv("INGEST_BATCH_BYTES", str(1 << 20)))


class MixedCloneStreamError(ValueError):
    """Raised when one stream carries frames for more than one clone.

    A batch is attributed to a single clone, so silently accepting a changed
    clone_id would misattribute every frame in the batch.
    """

    def __init__(self, expected: str, received: str) -> None:
        super().__init__(
            f"stream opened for clone_id {expected!r} but received {received!r}; "
            "one stream carries one clone"
        )
        self.expected = expected
        self.received = received


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
    payloads: list[bytes] = field(default_factory=list)


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
    payloads: list[bytes] = []
    stream_clone_id: str | None = None

    async for frame in request_iterator:
        frame_clone_id = getattr(frame, "clone_id", "") or ""

        # Pin on the first frame that actually carries a clone_id. proto3
        # delivers an unset string as "", so pinning on the first frame
        # regardless would lock the stream to "unknown" and then reject every
        # later frame that does name the clone.
        if frame_clone_id:
            if stream_clone_id is None:
                stream_clone_id = frame_clone_id
                stats.clone_id = frame_clone_id
            elif frame_clone_id != stream_clone_id:
                raise MixedCloneStreamError(stream_clone_id, frame_clone_id)

        payload = getattr(frame, "payload", b"") or b""

        stats.frames_received += 1
        stats.bytes_received += len(payload)
        frames += 1
        payload_size += len(payload)
        timestamps.append(getattr(frame, "captured_at_unix_ms", 0))
        payloads.append(payload)

        if frames >= max_frames or payload_size >= max_bytes:
            yield MicroBatch(
                stream_clone_id or stats.clone_id,
                frames,
                payload_size,
                timestamps,
                payloads,
            )
            frames = 0
            payload_size = 0
            timestamps = []
            payloads = []

    if frames:
        yield MicroBatch(stats.clone_id, frames, payload_size, timestamps, payloads)
