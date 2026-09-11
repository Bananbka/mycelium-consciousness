import os

from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

app = Celery(
    "clone_memory_worker",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)
app.conf.task_default_queue = "memory_batches"


@app.task(name="memory.process_batch")
def process_batch(clone_id: str, payload_size: int) -> dict[str, str | int]:
    return {
        "clone_id": clone_id,
        "payload_size": payload_size,
        "status": "queued_for_embedding",
    }
