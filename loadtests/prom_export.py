"""Dump server-side time series from Prometheus into a CSV (lab 8).

    uv run python loadtests/prom_export.py results/lab8-soak/prom.csv <start_epoch> <end_epoch>

Locust's `*_stats_history.csv` percentiles are cumulative over the whole run, so
they cannot show latency drifting during a soak test; the API's own request
histogram (windowed `rate()`) can. Run it while Prometheus is still up.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.parse
import urllib.request

PROM = "http://localhost:9090"
REQ = "http_request_duration_seconds"
QUERIES = {
    "rps": f"sum(rate({REQ}_count[30s]))",
    "p50": f"histogram_quantile(0.5, sum by (le) (rate({REQ}_bucket[1m])))",
    "p95": f"histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket[1m])))",
    "p99": f"histogram_quantile(0.99, sum by (le) (rate({REQ}_bucket[1m])))",
    # Everything except the argon2 endpoints: login bursts dominate the overall
    # percentiles during ramp-ups and hide how the rest of the API behaves.
    "p50_api": (
        f'histogram_quantile(0.5, sum by (le) (rate({REQ}_bucket{{path!="/auth/login"}}[1m])))'
    ),
    "p95_api": (
        f'histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket{{path!="/auth/login"}}[1m])))'
    ),
    "p99_api": (
        f'histogram_quantile(0.99, sum by (le) (rate({REQ}_bucket{{path!="/auth/login"}}[1m])))'
    ),
    "p95_login": (
        f'histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket{{path="/auth/login"}}[1m])))'
    ),
    "err_pct": (
        f'100 * sum(rate({REQ}_count{{status=~"5.."}}[1m])) '
        f"/ sum(rate({REQ}_count[1m])) or vector(0)"
    ),
    "pg_backends": "sum(pg_stat_database_numbackends)",
    "redis_mem_bytes": "redis_memory_used_bytes",
    "redis_clients": "redis_connected_clients",
    "nginx_active": "nginx_connections_active",
    "cpu_host_pct": "sum(container_cpu_percent) / scalar(docker_host_cpus)",
    "ram_host_pct": (
        "100 * sum(container_memory_bytes) / scalar(docker_host_memory_bytes)"
    ),
    "api_cpu": 'sum(container_cpu_percent{service="api"})',
    "api_ram_mib": 'sum(container_memory_bytes{service="api"}) / 1048576',
    "celery_cpu": 'sum(container_cpu_percent{service="celery-worker"})',
    "celery_ram_mib": 'sum(container_memory_bytes{service="celery-worker"}) / 1048576',
    "db_ram_mib": 'sum(container_memory_bytes{service="db"}) / 1048576',
    "redis_ram_mib": 'sum(container_memory_bytes{service="redis"}) / 1048576',
    "db_size_mib": 'sum(pg_database_size_bytes{datname!~"template.*|postgres"}) / 1048576',
    "nginx_ram_mib": 'sum(container_memory_bytes{service="nginx"}) / 1048576',
}


def query_range(expr: str, start: float, end: float, step: int) -> dict[float, float]:
    qs = urllib.parse.urlencode(
        {"query": expr, "start": start, "end": end, "step": step}
    )
    with urllib.request.urlopen(f"{PROM}/api/v1/query_range?{qs}", timeout=60) as r:
        result = json.load(r)["data"]["result"]
    if not result:
        return {}
    return {float(t): float(v) for t, v in result[0]["values"] if v != "NaN"}


def main() -> None:
    path, start, end = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    step = max(5, int((end - start) / 3000))  # stay under Prometheus' point limit
    series = {name: query_range(q, start, end, step) for name, q in QUERIES.items()}
    stamps = sorted({t for s in series.values() for t in s})
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t", *series])
        for t in stamps:
            writer.writerow([t - start, *[series[n].get(t, "") for n in series]])
    print(f"{path}: {len(stamps)} rows, step {step}s")


if __name__ == "__main__":
    main()
