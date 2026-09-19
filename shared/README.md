# Shared Package

Shared database and schema code used by the services.

It contains the SQLAlchemy async engine/session factory, ORM models, the JWT
primitives, the Redis stream helpers, the object storage client, and the
Alembic migration environment.

The importable package lives under `src/shared/` (src layout, built with
`uv_build`). Nothing outside `src/` is importable as `shared.*`.

## Streams

`shared.streams` is the hot write path: `append_memory` is one `XADD` and
returns the stream's new length so the caller can decide whether to trigger
an out-of-band flush. `read_all`/`trim_up_to` back the flush that moves a
stream's entries into `MemoryBuffer` rows; `active_clone_ids` lists every
clone with a non-empty stream, for the scheduled flush. `MEMORY_STREAM_MAXLEN`
is the per-stream safety valve a write checks against — not a passive Redis
`MAXLEN` trim (which would silently discard the oldest entries), but a
threshold that triggers an immediate flush instead.

## Object storage

`shared.object_storage` wraps a synchronous `boto3` S3 client in
`asyncio.to_thread` (`put_object`, `get_object`, `delete_object`,
`list_keys`), pointed at MinIO by default (`MINIO_ENDPOINT`) but usable
against any S3-compatible service. It only ever holds backup archives — a
clone's live, not-yet-backed-up memory is a `MemoryBuffer` row in Postgres,
reached via the Redis stream above, not written there directly.

`shared.backup_codec` packs a backup's entries with msgpack and gzips the
result only when that's smaller than the raw packed bytes.

## Connection pooling

The engine is created at import time, so any process that forks after importing
this package (Celery's prefork pool) must call `dispose_engine()` in the child.
`celery_worker.app` wires this to `worker_process_init`.

Pool size is per process: the cluster-wide ceiling is
`(DB_POOL_SIZE + DB_MAX_OVERFLOW) * process count`. Keep it under the server's
`max_connections`.
