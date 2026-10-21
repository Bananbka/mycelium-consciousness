"""Prometheus metrics, JSON request logs and fault injection for the API.

One middleware covers all three, so every request is counted, timed and logged
the same way. Labels stay low-cardinality: the route is the path *template*
(`/profiles/{profile_id}`), never the concrete URL.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.routing import Match

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency",
    ["route"],
)

# Probes and the scrape itself are neither counted nor failed on purpose.
SKIP_PATHS = {"/metrics", "/health", "/docs"}

_log = logging.getLogger("api.request")


def fail_rate() -> float:
    """Share of requests that answer 500, read per call so tests can change it."""
    try:
        return min(max(float(os.getenv("FAIL_RATE", "0")), 0.0), 1.0)
    except ValueError:
        return 0.0


def configure_json_logging() -> None:
    """Request logs go to stdout as one JSON object per line."""
    if _log.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    if route is None:
        # call_next did not run (injected failure): match the route ourselves.
        for candidate in request.app.router.routes:
            if candidate.matches(request.scope)[0] == Match.FULL:
                route = candidate
                break
    return getattr(route, "path", None) or "unknown"


def _record(request: Request, status: int, started: float) -> None:
    route = _route_template(request)
    duration = time.perf_counter() - started
    REQUESTS.labels(request.method, route, str(status)).inc()
    LATENCY.labels(route).observe(duration)
    _log.info(
        json.dumps(
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "level": "error" if status >= 500 else "info",
                "msg": "request",
                "method": request.method,
                "route": route,
                "status": status,
                "duration_ms": round(duration * 1000, 1),
                "version": os.getenv("APP_VERSION", "dev"),
            }
        )
    )


def install(app: FastAPI) -> None:
    configure_json_logging()

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in SKIP_PATHS:
            return await call_next(request)
        started = time.perf_counter()
        rate = fail_rate()
        if rate and random.random() < rate:
            response: Response = JSONResponse(
                {"error": "injected failure"}, status_code=500
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                _record(request, 500, started)
                raise
        _record(request, response.status_code, started)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
