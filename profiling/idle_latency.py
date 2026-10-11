"""Unloaded latency per layer: sequential requests on one keep-alive connection.

Run it *inside* the compose network (a host port is forwarded by Docker Desktop
and adds a flat ~40 ms to small POSTs that has nothing to do with the system):

    docker run --rm --network lab9_default -v "$PWD/profiling:/p" \
        python:3.12-slim python /p/idle_latency.py nginx:80 lab9-api-1:8000

Needs PROFILING_ENDPOINTS=1 and the lt-* accounts (k6 PREP). Prints p50/p95 in
ms per endpoint for every target; these are the per-hop numbers of the
request-flow table in docs/final-architecture.md.
"""

from __future__ import annotations

import http.client
import json
import statistics
import sys
import time

N = 300


def timed(conn, method: str, path: str, headers: dict, body: str | None) -> dict:
    for _ in range(30):  # warm connections, caches, prepared statements
        conn.request(method, path, body, headers)
        conn.getresponse().read()
    samples = []
    for _ in range(N):
        t0 = time.perf_counter()
        conn.request(method, path, body, headers)
        r = conn.getresponse()
        r.read()
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status < 400, (path, r.status)
    samples.sort()
    return {
        "p50": round(statistics.median(samples), 2),
        "p95": round(samples[int(0.95 * (len(samples) - 1))], 2),
    }


def measure(target: str) -> dict:
    host, port = target.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    conn.request(
        "POST",
        "/auth/login",
        json.dumps({"email": "lt-user-1@example.com", "password": "lt-pass-1-Zx9"}),
        {"content-type": "application/json"},
    )
    token = json.loads(conn.getresponse().read())["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    js = {**auth, "content-type": "application/json"}
    body = json.dumps({"content": "x" * 80})
    return {
        "noop (framework + middleware)": timed(conn, "GET", "/_prof/noop", {}, None),
        "+ redis ping": timed(conn, "GET", "/_prof/redis", {}, None),
        "+ db checkout + SELECT 1": timed(conn, "GET", "/_prof/db", {}, None),
        "+ JWT + user/profile lookup": timed(conn, "GET", "/_prof/auth", auth, None),
        "POST /memories/write": timed(conn, "POST", "/memories/write", js, body),
        "GET /memories/backups (cache HIT)": timed(
            conn, "GET", "/memories/backups?limit=50", auth, None
        ),
        "GET /memories/backups (cache BYPASS)": timed(
            conn,
            "GET",
            "/memories/backups?limit=50",
            {**auth, "Cache-Control": "no-cache"},
            None,
        ),
    }


if __name__ == "__main__":
    print(json.dumps({t: measure(t) for t in sys.argv[1:]}, indent=2))
