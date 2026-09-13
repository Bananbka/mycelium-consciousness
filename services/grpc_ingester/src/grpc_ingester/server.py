from __future__ import annotations

import asyncio
import logging
import os
import time

import grpc

from grpc_ingester.auth import (
    GrpcAuthenticationError,
    GrpcAuthorizationError,
    authenticate,
)
from grpc_ingester.dispatch import dispatch_batch
from grpc_ingester.handlers import (
    IngestionStats,
    MixedCloneStreamError,
    iter_micro_batches,
)
from shared.protos import memory_stream_pb2, memory_stream_pb2_grpc

logger = logging.getLogger(__name__)

GRACE_PERIOD_SECONDS = float(os.getenv("GRPC_SHUTDOWN_GRACE", "10"))
MAX_CONCURRENT_RPCS = int(os.getenv("GRPC_MAX_CONCURRENT_RPCS", "512"))
GRPC_COMPRESSION = (
    grpc.Compression.Gzip
    if os.getenv("GRPC_COMPRESSION", "none").lower() == "gzip"
    else grpc.Compression.NoCompression
)


class MemoryStreamService(memory_stream_pb2_grpc.MemoryStreamServicer):
    async def StreamMemories(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext,
    ) -> memory_stream_pb2.IngestionSummary:
        try:
            owner_user_id = authenticate(context)
        except GrpcAuthenticationError as exc:
            logger.warning("rejecting unauthenticated stream: %s", exc)
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, str(exc))
        except GrpcAuthorizationError as exc:
            logger.warning("rejecting stream: %s", exc)
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))

        stats = IngestionStats()
        started = time.monotonic()
        batches = 0

        logger.info(
            "stream opened from %s for user_id=%d", context.peer(), owner_user_id
        )

        try:
            async for batch in iter_micro_batches(request_iterator, stats):
                await dispatch_batch(
                    owner_user_id=owner_user_id,
                    payload_size=batch.payload_size,
                    frames=batch.frames,
                    payloads=batch.payloads,
                    captured_at_unix_ms=batch.captured_at_unix_ms,
                )
                batches += 1
                # Per batch, not per frame: one line per frame would be noise
                # at ingestion volume.
                logger.debug(
                    "dispatched batch %d for user_id=%d frames=%d bytes=%d",
                    batches,
                    owner_user_id,
                    batch.frames,
                    batch.payload_size,
                )
        except MixedCloneStreamError as exc:
            logger.warning("rejecting mixed-clone stream: %s", exc)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except Exception:
            logger.exception(
                "stream aborted for clone_id=%s after %d frames",
                stats.clone_id,
                stats.frames_received,
            )
            await context.abort(grpc.StatusCode.INTERNAL, "ingestion failed")

        elapsed = time.monotonic() - started
        logger.info(
            "stream closed clone_id=%s frames=%d bytes=%d batches=%d in %.3fs",
            stats.clone_id,
            stats.frames_received,
            stats.bytes_received,
            batches,
            elapsed,
        )

        return memory_stream_pb2.IngestionSummary(
            clone_id=stats.clone_id,
            frames_received=stats.frames_received,
            bytes_received=stats.bytes_received,
        )


async def serve() -> None:
    port = int(os.getenv("GRPC_PORT", "50051"))
    server = grpc.aio.server(
        maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS,
        compression=GRPC_COMPRESSION,
    )
    memory_stream_pb2_grpc.add_MemoryStreamServicer_to_server(
        MemoryStreamService(), server
    )

    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info("gRPC ingester listening on %s", listen_addr)

    try:
        await server.wait_for_termination()
    finally:
        await server.stop(GRACE_PERIOD_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(serve())
