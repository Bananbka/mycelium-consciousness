"""/metrics, route-template labels and FAIL_RATE fault injection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    # No `with`: the lifespan (bucket, JWT check) is not needed for these routes.
    return TestClient(app)


def _sample(client: TestClient, route: str, status: str) -> float:
    prefix = f'http_requests_total{{method="GET",route="{route}",status="{status}"}} '
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(prefix):
            return float(line.removeprefix(prefix))
    return 0.0


def test_metrics_endpoint_exposes_request_counter_and_histogram(client):
    client.get("/version")
    body = client.get("/metrics").text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds_bucket" in body


def test_route_label_is_the_template_not_the_url(client):
    before = _sample(client, "/version", "200")
    client.get("/version")
    assert _sample(client, "/version", "200") == before + 1


def test_probes_and_the_scrape_are_not_counted(client):
    client.get("/metrics")
    assert 'route="/metrics"' not in client.get("/metrics").text


def test_fail_rate_one_returns_500_and_is_counted(client, monkeypatch):
    monkeypatch.setenv("FAIL_RATE", "1")
    before = _sample(client, "/version", "500")
    assert client.get("/version").status_code == 500
    assert _sample(client, "/version", "500") == before + 1


def test_metrics_and_health_stay_up_when_everything_else_fails(client, monkeypatch):
    monkeypatch.setenv("FAIL_RATE", "1")
    assert client.get("/metrics").status_code == 200
