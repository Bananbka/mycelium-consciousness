"""Metrics publishing and fault injection, against a fake CloudWatch client."""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lb"))

from chaos import Chaos  # noqa: E402
from metrics import MetricsPublisher  # noqa: E402


class FakeCloudWatch:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    def put_metric_data(self, **kwargs) -> None:
        if self.fail:
            raise ConnectionError("cloudwatch unreachable")
        self.calls.append(kwargs)


def _publisher(client=None, now=1_700_000_000.0) -> MetricsPublisher:
    return MetricsPublisher(client or FakeCloudWatch(), "app-1", clock=lambda: now)


def _by_name(call: dict, dimensions: int) -> dict:
    """Metrics of one dimension set: 2 dimensions in both sets, told apart by name."""
    wanted = "Scope" if dimensions == 1 else "InstanceId"
    return {
        m["MetricName"]: m
        for m in call["MetricData"]
        if any(d["Name"] == wanted for d in m["Dimensions"])
    }


def test_flush_publishes_count_errors_and_duration_statistics() -> None:
    client = FakeCloudWatch()
    publisher = _publisher(client)
    for status, ms in [(200, 10), (200, 30), (404, 5), (500, 100), (503, 50)]:
        publisher.record(status, ms)

    assert publisher.flush() is True

    (call,) = client.calls
    assert call["Namespace"] == "Mycelium/Api"
    system = _by_name(call, dimensions=1)
    assert system["RequestCount"]["Value"] == 5
    assert system["Errors"]["Value"] == 2  # 5xx only
    assert system["ClientErrors"]["Value"] == 1  # 4xx
    duration = system["Duration"]["StatisticValues"]
    assert duration == {
        "SampleCount": 5,
        "Sum": 195.0,
        "Minimum": 5,
        "Maximum": 100,
    }
    assert system["Duration"]["Unit"] == "Milliseconds"


def test_each_metric_is_published_for_the_system_and_for_the_instance() -> None:
    client = FakeCloudWatch()
    publisher = _publisher(client)
    publisher.record(200, 1)
    publisher.flush()
    (call,) = client.calls
    per_instance = _by_name(call, dimensions=2)
    assert set(per_instance) == {"RequestCount", "Errors", "ClientErrors", "Duration"}
    dims = {d["Name"]: d["Value"] for d in per_instance["Errors"]["Dimensions"]}
    assert dims == {"Service": "mycelium-api", "InstanceId": "app-1"}
    total = {d["Name"]: d["Value"] for d in _by_name(call, 1)["Errors"]["Dimensions"]}
    assert total == {"Service": "mycelium-api", "Scope": "all"}


def test_timestamp_and_resolution() -> None:
    client = FakeCloudWatch()
    publisher = _publisher(client, now=1_700_000_000.0)
    publisher.record(200, 1)
    publisher.flush()
    metric = client.calls[0]["MetricData"][0]
    assert metric["Timestamp"] == datetime.fromtimestamp(1_700_000_000.0, UTC)
    assert metric["StorageResolution"] == 1  # high resolution: 10/30 s periods


def test_an_idle_window_sends_nothing() -> None:
    client = FakeCloudWatch()
    assert _publisher(client).flush() is False
    assert client.calls == []


def test_the_window_is_reset_after_a_flush() -> None:
    client = FakeCloudWatch()
    publisher = _publisher(client)
    publisher.record(200, 10)
    publisher.flush()
    publisher.record(500, 20)
    publisher.flush()
    second = _by_name(client.calls[1], dimensions=1)
    assert second["RequestCount"]["Value"] == 1
    assert second["Errors"]["Value"] == 1


def test_a_cloudwatch_failure_never_reaches_the_request_path() -> None:
    publisher = _publisher(FakeCloudWatch(fail=True))
    publisher.record(200, 10)
    assert publisher.flush() is False  # swallowed, window dropped, no exception


# ---- fault injection ----------------------------------------------------------------


def test_chaos_is_off_by_default() -> None:
    chaos = Chaos()
    assert chaos.state() == {"error_rate": 0.0, "latency_ms": 0.0}
    assert chaos.decide() == (0.0, False)


def test_chaos_latency_and_error_rate() -> None:
    chaos = Chaos(random.Random(1))
    chaos.configure({"error_rate": 1.0, "latency_ms": 250})
    assert chaos.decide() == (0.25, True)
    chaos.configure({"error_rate": 0.0})
    assert chaos.decide()[1] is False


def test_chaos_error_rate_is_probabilistic() -> None:
    chaos = Chaos(random.Random(7))
    chaos.configure({"error_rate": 0.3})
    failures = sum(chaos.decide()[1] for _ in range(2000))
    assert 500 < failures < 700  # ~30 %


def test_chaos_reset() -> None:
    chaos = Chaos()
    chaos.configure({"error_rate": 0.5, "latency_ms": 100})
    assert chaos.reset() == {"error_rate": 0.0, "latency_ms": 0.0}


@pytest.mark.parametrize(
    "body",
    [
        {"error_rate": 1.5},
        {"error_rate": -0.1},
        {"latency_ms": 20_000},
        {"latency_ms": "slow"},
        {"error_rate": True},
    ],
)
def test_chaos_rejects_invalid_settings(body: dict) -> None:
    with pytest.raises(ValueError):
        Chaos().configure(body)
