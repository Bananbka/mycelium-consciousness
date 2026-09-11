from __future__ import annotations

import asyncio
import logging
import os

import grpc

from grpc_ingester.dispatch import dispatch_batch
from grpc_ingester.handlers import IngestionStats, iter_micro_batches
from shared.protos import memory_stream_pb2, memory_stream_pb2_grpc

logger = logging.getLogger(__name__)

GRACE_PERIOD_SECONDS = float(os.getenv("GRPC_SHUTDOWN_GRACE", "10"))
MAX_CONCURRENT_RPCS = int(os.getenv("GRPC_MAX_CONCURRENT_RPCS", "512"))


class MemoryStreamService(memory_stream_pb2_grpc.MemoryStreamServicer):
    async def StreamMemories(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext,
    ) -> memory_stream_pb2.IngestionSummary:
        stats = IngestionStats()

        try:
            async for batch in iter_micro_batches(request_iterator, stats):
                await dispatch_batch(
                    clone_id=batch.clone_id,
                    payload_size=batch.payload_size,
                    frames=batch.frames,
                )
        except Exception:
            logger.exception(
                "stream aborted for clone_id=%s after %d frames",
                stats.clone_id,
                stats.frames_received,
            )
            await context.abort(grpc.StatusCode.INTERNAL, "ingestion failed")

        return memory_stream_pb2.IngestionSummary(
            clone_id=stats.clone_id,
            frames_received=stats.frames_received,
            bytes_received=stats.bytes_received,
        )


async def serve() -> None:
    port = int(os.getenv("GRPC_PORT", "50051"))
    server = grpc.aio.server(maximum_concurrent_rpcs=MAX_CONCURRENT_RPCS)
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
