import asyncio
import os

import grpc

from grpc_ingester.handlers import collect_stream


class MemoryStreamService:
    async def StreamMemories(self, request_iterator, context):
        stats = await collect_stream(request_iterator)
        return {
            "clone_id": stats.clone_id,
            "frames_received": stats.frames_received,
            "bytes_received": stats.bytes_received,
        }


async def serve() -> None:
    port = int(os.getenv("GRPC_PORT", "50051"))
    server = grpc.aio.server()
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)
    await server.start()
    await server.wait_for_termination()


def main() -> None:
    asyncio.run(serve())
