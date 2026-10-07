# ruff: noqa: E501
"""Regenerates infra/monitoring/grafana/dashboards/stability.json (run from the repo root)."""

import json

DS = {"type": "prometheus", "uid": "prometheus"}
SVC = "service"
panels = []
_id = 0


def ts(title, exprs, x, y, w=12, h=8, unit="short", stack=False, mn=None, mx=None):
    global _id
    _id += 1
    custom = {
        "lineWidth": 1,
        "fillOpacity": 10 if not stack else 40,
        "showPoints": "never",
    }
    if stack:
        custom["stacking"] = {"mode": "normal", "group": "A"}
    panels.append(
        {
            "id": _id,
            "type": "timeseries",
            "title": title,
            "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {
                "defaults": {"unit": unit, "custom": custom, "min": mn, "max": mx},
                "overrides": [],
            },
            "options": {
                "legend": {
                    "displayMode": "table",
                    "placement": "bottom",
                    "calcs": ["mean", "max"],
                }
            },
            "targets": [
                {"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": lf}
                for i, (e, lf) in enumerate(exprs)
            ],
        }
    )


def stat(title, expr, x, unit="short", thresholds=None, decimals=None):
    global _id
    _id += 1
    steps = [{"color": "green", "value": None}]
    if thresholds:
        steps += [{"color": c, "value": v} for c, v in thresholds]
    d = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}
    if decimals is not None:
        d["decimals"] = decimals
    panels.append(
        {
            "id": _id,
            "type": "stat",
            "title": title,
            "datasource": DS,
            "gridPos": {"x": x, "y": 0, "w": 4, "h": 4},
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "background",
            },
            "targets": [{"refId": "A", "datasource": DS, "expr": expr}],
        }
    )


REQ = "http_request_duration_seconds"
stat("Requests / s", f"sum(rate({REQ}_count[30s]))", 0, "reqps", decimals=0)
stat(
    "p95 latency",
    f"histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket[1m])))",
    4,
    "s",
    [("yellow", 0.5), ("red", 1)],
)
stat(
    "Error rate (5xx)",
    f'100 * sum(rate({REQ}_count{{status=~"5.."}}[1m])) / sum(rate({REQ}_count[1m])) or vector(0)',
    8,
    "percent",
    [("yellow", 0.05), ("red", 0.1)],
    decimals=3,
)
stat(
    "Containers CPU (% of host)",
    "sum(container_cpu_percent) / scalar(docker_host_cpus)",
    12,
    "percent",
    [("yellow", 60), ("red", 80)],
    decimals=1,
)
stat(
    "Containers RAM (% of host)",
    "100 * sum(container_memory_bytes) / scalar(docker_host_memory_bytes)",
    16,
    "percent",
    [("yellow", 60), ("red", 80)],
    decimals=1,
)
stat("Healthy API replicas", 'count(up{job="api"} == 1)', 20, decimals=0)

ts(
    "Throughput by status class",
    [(f"sum by (status) (rate({REQ}_count[30s]))", "{{status}}")],
    0,
    4,
    unit="reqps",
    stack=True,
)
ts(
    "Latency percentiles (API process)",
    [
        (
            f"histogram_quantile({q}, sum by (le) (rate({REQ}_bucket[1m])))",
            f"p{int(q * 100)}",
        )
        for q in (0.5, 0.95, 0.99)
    ],
    12,
    4,
    unit="s",
)
ts(
    "Requests per replica",
    [(f"sum by (instance) (rate({REQ}_count[30s]))", "{{instance}}")],
    0,
    12,
    unit="reqps",
)
ts(
    "Error rate, 5xx (target <= 0.1 %)",
    [
        (
            f'100 * sum(rate({REQ}_count{{status=~"5.."}}[1m])) / sum(rate({REQ}_count[1m])) or vector(0)',
            "5xx %",
        )
    ],
    12,
    12,
    unit="percent",
    mn=0,
)
ts(
    "Container CPU (% of one core)",
    [("sum by (service) (container_cpu_percent)", "{{service}}")],
    0,
    20,
    unit="percent",
)
ts(
    "Container RAM (working set)",
    [("sum by (service) (container_memory_bytes)", "{{service}}")],
    12,
    20,
    unit="bytes",
)
ts(
    "Open connections (nginx / redis / postgres)",
    [
        ("nginx_connections_active", "active connections"),
        ("redis_connected_clients", "redis clients"),
        ("sum(pg_stat_database_numbackends)", "postgres backends"),
    ],
    0,
    28,
)
ts(
    "Redis memory",
    [("redis_memory_used_bytes", "used memory")],
    12,
    28,
    unit="bytes",
)
ts(
    "nginx requests",
    [
        ("rate(nginx_http_requests_total[30s])", "nginx requests/s"),
    ],
    0,
    36,
    unit="reqps",
)
ts(
    "Postgres transactions / s and cache hit",
    [
        ("sum(rate(pg_stat_database_xact_commit[30s]))", "commits/s"),
        (
            "sum(rate(pg_stat_database_blks_hit[1m])) / (sum(rate(pg_stat_database_blks_hit[1m])) + sum(rate(pg_stat_database_blks_read[1m])))",
            "buffer hit ratio",
        ),
    ],
    12,
    36,
)

dash = {
    "uid": "clone-stability",
    "title": "Clone Factory - stability",
    "tags": ["lab8"],
    "timezone": "browser",
    "schemaVersion": 41,
    "refresh": "5s",
    "time": {"from": "now-15m", "to": "now"},
    "panels": panels,
}
json.dump(
    dash,
    open("infra/monitoring/grafana/dashboards/stability.json", "w", encoding="utf-8"),
    indent=2,
)
print(len(panels))
