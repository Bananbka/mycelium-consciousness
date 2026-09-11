# Shared Package

Shared database and schema code used by the services.

It contains the SQLAlchemy async engine/session factory, ORM models, pgvector
fields, protobuf contracts, and the Alembic migration environment.

The importable package lives under `src/shared/` (src layout, built with
`uv_build`). Nothing outside `src/` is importable as `shared.*`.

## Protobuf stubs

`src/shared/protos/memory_stream.proto` is the contract. The generated
`*_pb2*.py` modules are gitignored — regenerate them with:

```bash
uv run python -m shared.protos.generate
```

`--proto_path` is the `src` root so generated imports resolve as
`shared.protos.memory_stream_pb2` rather than a bare top-level module.

## Connection pooling

The engine is created at import time, so any process that forks after importing
this package (Celery's prefork pool) must call `dispose_engine()` in the child.
`celery_worker.app` wires this to `worker_process_init`.

Pool size is per process: the cluster-wide ceiling is
`(DB_POOL_SIZE + DB_MAX_OVERFLOW) * process count`. Keep it under the server's
`max_connections`.
