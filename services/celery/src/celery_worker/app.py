"""Celery application: configuration and worker-process lifecycle.

Task definitions live in celery_worker.tasks; they are pulled in via `include`
rather than imported here, which would be circular.
"""

import asyncio
import os
import threading

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from shared import object_storage
from shared.db import dispose_engine

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
TASK_QUEUE = os.getenv("CELERY_TASK_QUEUE", "memory_batches")

app = Celery(
    "clone_memory_worker",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["celery_worker.tasks"],
)
app.conf.task_default_queue = TASK_QUEUE
app.conf.task_serializer = "msgpack"
app.conf.accept_content = ["msgpack"]
app.conf.result_serializer = "msgpack"
app.conf.worker_prefetch_multiplier = 1
app.conf.task_acks_late = True
app.conf.broker_connection_retry_on_startup = True

ROLLUP_CHECK_INTERVAL_SECONDS = int(os.getenv("ROLLUP_CHECK_INTERVAL_SECONDS", "3600"))
STREAM_FLUSH_INTERVAL_SECONDS = int(os.getenv("STREAM_FLUSH_INTERVAL_SECONDS", "60"))
app.conf.beat_schedule = {
    "flush-due-streams": {
        "task": "memory.flush_due_streams",
        "schedule": STREAM_FLUSH_INTERVAL_SECONDS,
    },
    "check-due-memory-rollups": {
        "task": "memory.rollup_due_clones",
        "schedule": ROLLUP_CHECK_INTERVAL_SECONDS,
    },
}

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def run_async(coro):
    """Run a coroutine on this worker process's own persistent event loop.

    asyncio.run() would build and tear down a loop per task, and pooled asyncpg
    connections are bound to the loop that created them; reusing them from a
    second loop raises "attached to a different loop".

    The lock is a no-op under the prefork pool (one task per process at a
    time). It matters for --pool=threads/gevent, where two tasks would
    otherwise call run_until_complete on the same loop and the second would
    raise "This event loop is already running".
    """
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
        return _loop.run_until_complete(coro)


@worker_process_init.connect
def _reset_db_pool(**_kwargs) -> None:
    run_async(dispose_engine())
    run_async(object_storage.ensure_bucket())


@worker_process_shutdown.connect
def _close_db_pool(**_kwargs) -> None:
    global _loop
    run_async(dispose_engine())
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            _loop.close()
            _loop = None
