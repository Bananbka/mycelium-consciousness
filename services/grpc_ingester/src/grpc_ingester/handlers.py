from dataclasses import dataclass


@dataclass(slots=True)
class IngestionStats:
    clone_id: str = "unknown"
    frames_received: int = 0
    bytes_received: int = 0


async def collect_stream(request_iterator) -> IngestionStats:
    stats = IngestionStats()

    async for frame in request_iterator:
        stats.clone_id = getattr(frame, "clone_id", stats.clone_id) or stats.clone_id
        payload = getattr(frame, "payload", b"")
        stats.frames_received += 1
        stats.bytes_received += len(payload)

    return stats
