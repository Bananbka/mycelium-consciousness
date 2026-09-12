# Celery Worker

Background worker service for memory micro-batch processing.

## Layout

```
celery_worker/
  app.py         Celery instance, config, worker-process lifecycle
  pipeline.py    business logic, free of Celery imports so it is directly testable
  tasks/         task definitions, registered via `include` in app.py
    memory.py    memory.process_batch
```

Add a task module under `tasks/` and import it in `tasks/__init__.py`; `include`
in `app.py` does the rest.

The worker consumes `memory.process_batch` from the `memory_batches` queue,
dispatched by the gRPC ingester. Future iterations will chunk memories, call the
embedding provider, and upsert vectors into PostgreSQL.

`worker_prefetch_multiplier` is 1 and `task_acks_late` is on: embedding calls are
slow, so the default prefetch of 4 would make a single worker a head-of-line
blocker.

The shared SQLAlchemy engine is created at import time, before the prefork pool
forks. `worker_process_init` disposes it in each child so no connection socket is
shared across processes.
