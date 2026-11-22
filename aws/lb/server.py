"""The lab 8 function as a long-running HTTP service (one container = one instance).

Same ``handler.lambda_handler`` as in Lambda, behind a tiny HTTP server, so the
code is identical in both deployments. Every response carries ``X-Instance-ID``
which is how a client (and the load balancer demo) sees *who* answered.

    PYTHONPATH=aws/lambda STORE=memory python aws/lb/server.py   # no AWS (development)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import handler

INSTANCE_ID = os.getenv("INSTANCE_ID") or socket.gethostname()
PORT = int(os.getenv("PORT", "8080"))

if os.getenv("STORE") == "memory":
    from inmemory import FakeBlobs, FakeDb
    from service import Service

    handler.set_service(Service(FakeDb(), FakeBlobs()))


class Context:
    def __init__(self) -> None:
        self.aws_request_id = str(uuid.uuid4())


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive towards nginx

    def _serve(self) -> None:
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        path = self.path.split("?")[0]
        event = {"httpMethod": self.command, "path": path, "body": body}
        response = handler.lambda_handler(event, Context())

        payload = response["body"]
        if path == "/health" and payload:  # say which instance this is
            payload = json.dumps({**json.loads(payload), "instance": INSTANCE_ID})
        data = payload.encode()

        self.send_response(response["statusCode"])
        for key, value in response["headers"].items():
            self.send_header(key, value)
        self.send_header("X-Instance-ID", INSTANCE_ID)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        print(
            json.dumps(
                {
                    "instance": INSTANCE_ID,
                    "method": self.command,
                    "path": path,
                    "status": response["statusCode"],
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            ),
            flush=True,
        )

    do_GET = do_POST = do_DELETE = do_PUT = _serve

    def log_message(self, fmt: str, *args) -> None:  # we log JSON ourselves
        pass


if __name__ == "__main__":
    print(f"instance {INSTANCE_ID} listening on :{PORT}", file=sys.stderr, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Gateway).serve_forever()
