"""The lab 8 function as a long-running HTTP service (one container = one instance).

Same ``handler.lambda_handler`` as in Lambda, behind a tiny HTTP server, so the
code is identical in both deployments. Every response carries ``X-Instance-ID``
which is how a client (and the load balancer demo) sees *who* answered.

Lab 10 adds, both opt-in through environment variables:

* ``METRICS_ENABLED=1`` - request count, errors and duration are published to
  CloudWatch (``metrics.py``); health probes are not counted.
* ``CHAOS_ENABLED=1`` - ``/admin/chaos`` can inject latency / 5xx failures.

    PYTHONPATH=aws/lambda STORE=memory python aws/lb/server.py   # no AWS (development)
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handler
from chaos import Chaos
from metrics import MetricsPublisher

INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()
PORT = int(os.getenv("PORT", "8080"))
UNMETERED = {"/health", "/admin/chaos"}  # probes and operator calls are not traffic

chaos = Chaos() if os.getenv("CHAOS_ENABLED") == "1" else None
metrics = MetricsPublisher.from_env(INSTANCE_ID)

if os.getenv("STORE") == "memory":
    from inmemory import FakeBlobs, FakeDb
    from service import Service

    handler.set_service(Service(FakeDb(), FakeBlobs()))


class Context:
    def __init__(self) -> None:
        self.aws_request_id = str(uuid.uuid4())


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive towards nginx

    def _reply(self, status: int, payload: str, headers: dict | None = None) -> None:
        data = payload.encode()
        self.send_response(status)
        for key, value in (headers or {"Content-Type": "application/json"}).items():
            self.send_header(key, value)
        self.send_header("X-Instance-ID", INSTANCE_ID)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admin_chaos(self, body: str | None) -> tuple[int, str]:
        if chaos is None:
            return 404, json.dumps({"detail": "chaos is disabled"})
        try:
            if self.command == "GET":
                state = chaos.state()
            elif self.command == "DELETE":
                state = chaos.reset()
            elif self.command == "POST":
                state = chaos.configure(json.loads(body or "{}"))
            else:
                return 405, json.dumps({"detail": "method not allowed"})
        except (ValueError, json.JSONDecodeError) as exc:
            return 422, json.dumps({"detail": str(exc)})
        return 200, json.dumps({**state, "instance": INSTANCE_ID})

    def _serve(self) -> None:
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        path = self.path.split("?")[0]

        if path == "/admin/chaos":
            status, payload = self._admin_chaos(body)
            self._reply(status, payload)
            return

        headers = None
        failed_by_chaos = False
        if chaos is not None and path not in UNMETERED:
            delay, failed_by_chaos = chaos.decide()
            if delay:
                time.sleep(delay)  # counted in the measured duration, on purpose
        if failed_by_chaos:
            status = 500
            payload = json.dumps({"detail": "injected failure"})
        else:
            event = {"httpMethod": self.command, "path": path, "body": body}
            response = handler.lambda_handler(event, Context())
            status, payload, headers = (
                response["statusCode"],
                response["body"],
                response["headers"],
            )
            if path == "/health" and payload:  # say which instance this is
                payload = json.dumps({**json.loads(payload), "instance": INSTANCE_ID})

        self._reply(status, payload, headers)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if metrics is not None and path not in UNMETERED:
            metrics.record(status, elapsed_ms)
        print(
            json.dumps(
                {
                    "instance": INSTANCE_ID,
                    "method": self.command,
                    "path": path,
                    "status": status,
                    "ms": round(elapsed_ms, 1),
                }
            ),
            flush=True,
        )

    do_GET = do_POST = do_DELETE = do_PUT = _serve

    def log_message(self, fmt: str, *args) -> None:  # we log JSON ourselves
        pass


if __name__ == "__main__":
    print(f"instance {INSTANCE_ID} listening on :{PORT}", file=sys.stderr, flush=True)
    if metrics is not None:
        metrics.start()
        print(
            f"publishing metrics to CloudWatch every {metrics.flush_seconds}s",
            file=sys.stderr,
            flush=True,
        )
    # docker stop sends SIGTERM: exit cleanly so the last metrics window is flushed
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Gateway)
    try:
        server.serve_forever()
    finally:
        if metrics is not None:
            metrics.stop()
