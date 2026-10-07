"""Prometheus metrics for the API.

uvicorn runs several worker processes, so metrics are written to files under
PROMETHEUS_MULTIPROC_DIR and merged at scrape time (prometheus_client's
multiprocess mode). Without that variable the default in-process registry is
used (tests, single-process runs).

`/metrics` is meant for Prometheus on the internal compose network; the nginx
gateway answers 404 for it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency as seen by the API process",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
REQUESTS_FAILED = Counter(
    "http_requests_failed_total",
    "Requests answered with a 5xx status",
    ["method", "path"],
)


def reset_multiprocess_dir() -> None:
    """Drop metric files left by a previous run; call before workers start."""
    directory = os.getenv(MULTIPROC_ENV)
    if not directory:
        return
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    for stale in path.glob("*.db"):
        stale.unlink()


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    # The template ("/memories/backups/{backup_id}"), not the raw path, so
    # label cardinality stays bounded.
    return getattr(route, "path", "unmatched")


async def observe(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        path = _route_template(request)
        REQUEST_SECONDS.labels(request.method, path, str(status_code)).observe(
            time.perf_counter() - started
        )
        if status_code >= 500:
            REQUESTS_FAILED.labels(request.method, path).inc()


def render() -> Response:
    if os.getenv(MULTIPROC_ENV):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
