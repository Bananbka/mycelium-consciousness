from __future__ import annotations

from celery import Celery

from shared.settings import settings

BROKER_URL = settings.celery_broker_url
RESULT_BACKEND = settings.celery_result_backend
TASK_QUEUE = settings.celery_task_queue

_client = Celery("clone_memory_api_client", broker=BROKER_URL, backend=RESULT_BACKEND)
_client.conf.task_default_queue = TASK_QUEUE
_client.conf.task_serializer = "msgpack"
_client.conf.accept_content = ["msgpack"]
_client.conf.result_serializer = "msgpack"


def force_rollup_clone(clone_id: int, timeout: float = 15.0) -> dict[str, str | int]:
    result = _client.send_task("memory.force_rollup_clone", args=[clone_id])
    return result.get(timeout=timeout)


def flush_clone_stream(clone_id: int) -> None:
    """Fire-and-forget: ask a worker to drain one clone's stream now.

    Used when a write pushes a stream past MEMORY_STREAM_MAXLEN, so a single
    chatty clone gets flushed out-of-band instead of waiting for the next
    scheduled flush. Never awaited for a result — the write path must not
    block on it.
    """
    _client.send_task("memory.flush_clone_stream", args=[clone_id])
