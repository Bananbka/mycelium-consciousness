# Celery Worker

Background service for draining Redis into Postgres and folding Postgres into
periodic backups.

## Layout

```
celery_worker/
  app.py         Celery instance, beat schedule, worker-process lifecycle
  flush.py       Redis -> memory_buffers, free of Celery imports
  pipeline.py    memory_buffers -> MinIO backups, free of Celery imports
  tasks/         task definitions, registered via `include` in app.py
    memory.py    memory.flush_due_streams, memory.flush_clone_stream,
                 memory.rollup_due_clones, memory.force_rollup_clone
```

Add a task module under `tasks/` and import it in `tasks/__init__.py`; `include`
in `app.py` does the rest.

A write is a plain Redis `XADD`, called straight from the API's fast write
endpoint (`shared.streams.append_memory`) — no Postgres round trip on that
path. Four tasks move data along from there:

- `memory.flush_due_streams` runs on `celery-beat`'s
  `STREAM_FLUSH_INTERVAL_SECONDS` schedule (default 60s) and drains every
  clone's Redis stream into `MemoryBuffer` rows.
- `memory.flush_clone_stream(clone_id)` does the same for one clone
  immediately — dispatched fire-and-forget from the API write path when a
  stream's length crosses `MEMORY_STREAM_MAXLEN`, so one chatty clone gets
  flushed out-of-band instead of waiting for the schedule.
- `memory.rollup_due_clones` runs on `celery-beat`'s
  `ROLLUP_CHECK_INTERVAL_SECONDS` schedule (default hourly) and folds every
  clone whose subscription tier says it is due into one backup object in
  MinIO, then deletes the archived rows out of `memory_buffers`.
- `memory.force_rollup_clone(clone_id)` does the same for one clone
  immediately, ignoring its due date — triggered from the API's
  `POST /admin/clones/{id}/rollup`, for testing and ops.

`celery-beat` runs as its own compose service, sharing this image but
overriding the command to `celery ... beat` instead of `worker`. Running beat
embedded in a worker process (`-B`) is deliberately avoided here: it silently
duplicates the schedule if the worker is ever scaled to more than one replica.

The shared SQLAlchemy engine is created at import time, before the prefork pool
forks. `worker_process_init` disposes it in each child so no connection socket is
shared across processes.
