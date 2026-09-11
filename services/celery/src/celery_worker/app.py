import asyncio
import os

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from shared.db import dispose_engine

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
TASK_QUEUE = os.getenv("CELERY_TASK_QUEUE", "memory_batches")

app = Celery(
    "clone_memory_worker",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)
app.conf.task_default_queue = TASK_QUEUE
app.conf.worker_prefetch_multiplier = 1
app.conf.task_acks_late = True
app.conf.broker_connection_retry_on_startup = True


@worker_process_init.connect
def _reset_db_pool(**_kwargs) -> None:
    asyncio.run(dispose_engine())


@worker_process_shutdown.connect
def _close_db_pool(**_kwargs) -> None:
    asyncio.run(dispose_engine())


@app.task(name="memory.process_batch")
def process_batch(
    clone_id: str,
    payload_size: int,
    frames: int = 0,
) -> dict[str, str | int]:
    return {
        "clone_id": clone_id,
        "payload_size": payload_size,
        "frames": frames,
        "status": "queued_for_embedding",
    }
