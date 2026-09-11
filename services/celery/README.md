# Celery Worker

Background worker service for memory micro-batch processing.

The worker consumes `memory.process_batch` from the `memory_batches` queue,
dispatched by the gRPC ingester. Future iterations will chunk memories, call the
embedding provider, and upsert vectors into PostgreSQL.

`worker_prefetch_multiplier` is 1 and `task_acks_late` is on: embedding calls are
slow, so the default prefetch of 4 would make a single worker a head-of-line
blocker.

The shared SQLAlchemy engine is created at import time, before the prefork pool
forks. `worker_process_init` disposes it in each child so no connection socket is
shared across processes.
