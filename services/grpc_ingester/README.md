# gRPC Ingester

Asynchronous ingestion service for clone implant memory streams.

The protobuf contract is defined in
`shared/src/shared/protos/memory_stream.proto`. Generated stubs are not
committed — run `uv run python -m shared.protos.generate` before starting the
service locally (the Docker build does this during the image build).

`StreamMemories` consumes the client stream, accumulates frames into
micro-batches (`INGEST_BATCH_FRAMES` / `INGEST_BATCH_BYTES`), and hands each
batch to the Celery workers over the Redis broker by task name, so the two
services stay independently deployable. The RPC returns an `IngestionSummary`
counting the frames and bytes accepted.

Note that nginx routes gRPC by the `/memory_stream.MemoryStream/` path prefix
and requires `http2 on` — a plain path prefix like `/grpc` never matches.
