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

## Authentication

The call requires a bearer token in gRPC metadata, the same JWT issued by
`POST /auth/login`:

```
authorization: Bearer <access_token>
```

Missing or invalid tokens abort with `UNAUTHENTICATED`; a token whose role is
not `clone` (an admin token, for instance) aborts with `PERMISSION_DENIED`.
The memory owner is always the token's subject, resolved to that user's
`CloneProfile` in `store_batch` — the `clone_id` field on `MemoryFrame` is
never used to decide which profile is written to, so a caller cannot write
into another clone's memory stream by naming it. That field remains only for
detecting a client bug that mixes frames from more than one clone in a single
stream, and it is echoed back informationally in `IngestionSummary`.

There is still no transport encryption (`add_insecure_port`); this closes the
identity-forgery gap but not the plaintext-on-the-wire one.
