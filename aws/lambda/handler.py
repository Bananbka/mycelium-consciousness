"""AWS Lambda entry point: API Gateway (REST, proxy integration) -> Service.

API Gateway routes by resource + method, the function repeats that table so a
request is dispatched on ``event["resource"]`` (the template path such as
``/clones/{clone_id}``) and ``event["httpMethod"]``.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any

from service import ApiError, Route, Service

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_service: Service | None = None


def get_service() -> Service:
    """Built once per execution environment (reused by warm invocations)."""
    global _service
    if _service is None:
        from stores import DynamoDb, S3Blobs

        _service = Service(DynamoDb(), S3Blobs())
    return _service


def set_service(service: Service | None) -> None:
    """Test hook: inject a Service with in-memory stores."""
    global _service
    _service = service


# ---- routes: (method, resource template) -> (status, body) --------------------


def _health(_s: Service, _p: dict, _b: dict) -> tuple[int, Any]:
    return 200, {"status": "healthy"}


ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/health"): _health,
    ("POST", "/clones"): lambda s, p, b: (201, s.create_clone(b)),
    ("GET", "/clones/{clone_id}"): lambda s, p, b: (200, s.get_clone(p["clone_id"])),
    ("DELETE", "/clones/{clone_id}"): lambda s, p, b: (
        204,
        s.delete_clone(p["clone_id"]),
    ),
    ("POST", "/clones/{clone_id}/memories"): lambda s, p, b: (
        202,
        s.write_memory(p["clone_id"], b),
    ),
    ("GET", "/clones/{clone_id}/memories/status"): lambda s, p, b: (
        200,
        s.stream_status(p["clone_id"]),
    ),
    ("POST", "/clones/{clone_id}/backups"): lambda s, p, b: (
        201,
        s.rollup(p["clone_id"]),
    ),
    ("GET", "/clones/{clone_id}/backups"): lambda s, p, b: (
        200,
        s.list_backups(p["clone_id"]),
    ),
    ("GET", "/clones/{clone_id}/backups/{backup_id}"): lambda s, p, b: (
        200,
        s.get_backup(p["clone_id"], p["backup_id"]),
    ),
    ("DELETE", "/clones/{clone_id}/backups/{backup_id}"): lambda s, p, b: (
        204,
        s.delete_backup(p["clone_id"], p["backup_id"]),
    ),
}

_TEMPLATES = {
    resource: re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", resource) + "$")
    for _method, resource in ROUTES
}


def _match(event: dict) -> tuple[str, dict]:
    """Resource template and path parameters (path matching is the fallback)."""
    resource = event.get("resource")
    params = event.get("pathParameters") or {}
    if resource in _TEMPLATES.keys() and params is not None:
        return resource, params
    path = event.get("path", "")
    for template, regex in _TEMPLATES.items():
        found = regex.match(path)
        if found:
            return template, found.groupdict()
    return path, {}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):  # DynamoDB numbers
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not serialisable: {type(value).__name__}")


def _body(event: dict) -> dict:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "request body is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise ApiError(400, "request body must be a JSON object")
    return parsed


def _response(status: int, payload: Any, request_id: str = "") -> dict:
    headers = {"Content-Type": "application/json", "X-Request-Id": request_id}
    body = "" if status == 204 else json.dumps(payload, default=_json_default)
    return {"statusCode": status, "headers": headers, "body": body}


def lambda_handler(event: dict, context: Any) -> dict:
    request_id = getattr(context, "aws_request_id", "")
    method = event.get("httpMethod", "")
    try:
        resource, params = _match(event)
        route = ROUTES.get((method, resource))
        if route is None:
            known = any(r == resource for _m, r in ROUTES)
            raise ApiError(405 if known else 404, "no such route")
        status, payload = route(get_service(), params, _body(event))
    except ApiError as exc:
        status, payload = exc.status, {"detail": exc.detail}
    except Exception:
        logger.exception("unhandled error on %s %s", method, event.get("path"))
        status, payload = 500, {"detail": "internal error"}
    logger.info(
        json.dumps({"method": method, "path": event.get("path"), "status": status})
    )
    return _response(status, payload, request_id)
