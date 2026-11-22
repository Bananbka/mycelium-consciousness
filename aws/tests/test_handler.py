"""The Lambda contract: API Gateway proxy events in, proxy responses out."""

from __future__ import annotations

import json

import pytest

RESOURCES = {
    "/health": [],
    "/clones": [],
    "/clones/{clone_id}": ["clone_id"],
    "/clones/{clone_id}/memories": ["clone_id"],
    "/clones/{clone_id}/memories/status": ["clone_id"],
    "/clones/{clone_id}/backups": ["clone_id"],
    "/clones/{clone_id}/backups/{backup_id}": ["clone_id", "backup_id"],
}


class Ctx:
    aws_request_id = "req-1"


def call(wired, method, resource, params=None, body=None, raw_body=None):
    path = resource
    for key, value in (params or {}).items():
        path = path.replace("{" + key + "}", value)
    event = {
        "httpMethod": method,
        "resource": resource,
        "path": path,
        "pathParameters": params or None,
        "body": raw_body if raw_body is not None else json.dumps(body or {}),
    }
    response = wired.lambda_handler(event, Ctx())
    parsed = json.loads(response["body"]) if response["body"] else None
    return response["statusCode"], parsed, response


def test_health(wired) -> None:
    status, body, response = call(wired, "GET", "/health")
    assert (status, body) == (200, {"status": "healthy"})
    assert response["headers"]["X-Request-Id"] == "req-1"


def test_full_create_get_delete_flow(wired) -> None:
    status, body, _ = call(wired, "POST", "/clones", body={"designation": "alpha"})
    assert status == 201 and body["clone_id"] == "alpha"

    status, body, _ = call(wired, "GET", "/clones/{clone_id}", {"clone_id": "alpha"})
    assert status == 200 and body["designation"] == "alpha"

    mem = "/clones/{clone_id}/memories"
    for i in range(2):
        status, _, _ = call(
            wired, "POST", mem, {"clone_id": "alpha"}, body={"content": f"m{i}"}
        )
        assert status == 202
    status, body, _ = call(wired, "GET", mem + "/status", {"clone_id": "alpha"})
    assert (status, body) == (200, {"buffered_frames": 2})

    status, backup, _ = call(
        wired, "POST", "/clones/{clone_id}/backups", {"clone_id": "alpha"}
    )
    assert status == 201 and backup["entry_count"] == 2

    one = "/clones/{clone_id}/backups/{backup_id}"
    ids = {"clone_id": "alpha", "backup_id": backup["backup_id"]}
    status, body, _ = call(wired, "GET", one, ids)
    assert status == 200 and [e["content"] for e in body["payload"]] == ["m0", "m1"]

    status, body, response = call(wired, "DELETE", one, ids)
    assert (status, body, response["body"]) == (204, None, "")
    assert call(wired, "GET", one, ids)[0] == 404

    assert call(wired, "DELETE", "/clones/{clone_id}", {"clone_id": "alpha"})[0] == 204
    assert call(wired, "GET", "/clones/{clone_id}", {"clone_id": "alpha"})[0] == 404


def test_errors_are_json_with_the_right_status(wired) -> None:
    call(wired, "POST", "/clones", body={"designation": "alpha"})
    assert call(wired, "POST", "/clones", body={"designation": "alpha"})[0] == 409
    assert call(wired, "POST", "/clones", body={"designation": "A B"})[0] == 422
    status, body, _ = call(wired, "POST", "/clones", raw_body="{not json")
    assert status == 400 and "JSON" in body["detail"]
    status, _, _ = call(wired, "POST", "/clones", raw_body="[1, 2]")
    assert status == 400


def test_unknown_route_and_wrong_method(wired) -> None:
    assert call(wired, "GET", "/nope")[0] == 404
    assert call(wired, "PUT", "/clones")[0] == 405


def test_resource_is_recovered_from_the_path_when_missing(wired) -> None:
    call(wired, "POST", "/clones", body={"designation": "alpha"})
    event = {
        "httpMethod": "GET",
        "path": "/clones/alpha",
        "body": None,
    }  # no "resource"/"pathParameters", as some test invokers send
    response = wired.lambda_handler(event, Ctx())
    assert response["statusCode"] == 200


def test_unexpected_failures_become_500_without_leaking(wired, service) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("secret internals")

    service.db.get_clone = boom
    status, body, _ = call(wired, "GET", "/clones/{clone_id}", {"clone_id": "alpha"})
    assert status == 500 and body == {"detail": "internal error"}


@pytest.mark.parametrize("resource", list(RESOURCES))
def test_every_route_template_is_known(wired, resource) -> None:
    """Keep the deploy script and the handler in sync (see deploy.ps1)."""
    methods = {m for m, r in wired.ROUTES if r == resource}
    assert methods, f"{resource} has no handler"
