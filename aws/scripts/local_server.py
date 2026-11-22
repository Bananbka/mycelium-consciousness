"""Run the Lambda handler as a plain HTTP server, with in-memory stores.

Lets you try the API (and ``smoke.ps1``) without Docker or LocalStack:

    python aws/scripts/local_server.py            # http://127.0.0.1:8099
    .\aws\scripts\smoke.ps1 -ApiUrl http://127.0.0.1:8099

It builds the same API Gateway proxy event the real gateway sends, so it
exercises ``handler.lambda_handler`` exactly as in AWS.
"""

from __future__ import annotations

import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))

import handler  # noqa: E402
from inmemory import FakeBlobs, FakeDb  # noqa: E402
from service import Service  # noqa: E402

handler.set_service(Service(FakeDb(), FakeBlobs()))


class Context:
    def __init__(self) -> None:
        self.aws_request_id = str(uuid.uuid4())


class Gateway(BaseHTTPRequestHandler):
    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        event = {
            "httpMethod": self.command,
            "path": self.path.split("?")[0],
            "body": body,
        }
        response = handler.lambda_handler(event, Context())
        payload = response["body"].encode()
        self.send_response(response["statusCode"])
        for key, value in response["headers"].items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = do_PUT = _serve

    def log_message(self, fmt: str, *args) -> None:  # one line per request
        print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"serving http://127.0.0.1:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Gateway).serve_forever()
