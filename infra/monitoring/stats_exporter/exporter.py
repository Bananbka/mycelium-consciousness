"""Per-container CPU/RAM exporter for Prometheus, via the Docker API.

cAdvisor only sees the root cgroup under Docker Desktop (WSL2), so per-service
numbers come from the same source `docker stats` uses. Stdlib only.

Metrics (label `service` = compose service, `container` = container name):
  container_cpu_percent        100 = one fully used core (like `docker stats`)
  container_memory_bytes       usage minus page cache (like `docker stats`)
  docker_host_cpus, docker_host_memory_bytes
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

SOCKET = "/var/run/docker.sock"
PROJECT = os.getenv("COMPOSE_PROJECT", "clone-sandbox")
INTERVAL = float(os.getenv("SCRAPE_INTERVAL", "5"))

_lock = threading.Lock()
_body = b""


class _Conn(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("docker", timeout=15)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(SOCKET)


def _get(path: str):
    conn = _Conn()
    try:
        conn.request("GET", path)
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def _one(container: dict) -> list[str]:
    stats = _get(f"/containers/{container['Id']}/stats?stream=false")
    cpu, pre = stats["cpu_stats"], stats["precpu_stats"]
    cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    cpus = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", [1]))
    percent = 100.0 * cpu_delta / sys_delta * cpus if sys_delta > 0 else 0.0
    mem = stats["memory_stats"]
    used = mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0)
    labels = container["Labels"]
    service = labels.get("com.docker.compose.service", "")
    name = container["Names"][0].lstrip("/")
    lab = f'service="{service}",container="{name}"'
    return [
        f"container_cpu_percent{{{lab}}} {percent:.3f}",
        f"container_memory_bytes{{{lab}}} {max(used, 0)}",
    ]


def _loop() -> None:
    global _body
    flt = urllib.parse.quote(
        json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]})
    )
    while True:
        started = time.time()
        try:
            info = _get("/info")
            containers = _get(f"/containers/json?filters={flt}")
            lines = [
                f"docker_host_cpus {info['NCPU']}",
                f"docker_host_memory_bytes {info['MemTotal']}",
            ]
            with ThreadPoolExecutor(max_workers=16) as pool:
                for part in pool.map(_one, containers):
                    lines += part
            with _lock:
                _body = ("\n".join(lines) + "\n").encode()
        except Exception as exc:  # keep serving the last good sample
            print("scrape failed:", exc, flush=True)
        time.sleep(max(0.0, INTERVAL - (time.time() - started)))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        with _lock:
            body = _body
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(target=_loop, daemon=True).start()
    HTTPServer(("0.0.0.0", 9200), _Handler).serve_forever()
