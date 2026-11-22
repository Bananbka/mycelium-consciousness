"""Custom application metrics published to Amazon CloudWatch (``PutMetricData``).

Every request is recorded in memory; a background thread flushes the window every
few seconds as *statistic sets* (count / sum / min / max), so a busy instance sends
one small call instead of one call per request. Two dimension sets are published
per metric: ``Service`` + ``Scope=all`` (the whole system, summed over instances) and
``Service`` + ``InstanceId`` (per instance).

======================  =========================================================
metric                  meaning
======================  =========================================================
``RequestCount``        requests handled in the window
``Errors``              responses with a 5xx status (server-side failures)
``ClientErrors``        responses with a 4xx status (bad requests, 404, 409 ...)
``Duration``            handling time in ms (statistic set; Average = Sum/Count)
======================  =========================================================

Publishing must never hurt the service: every failure is logged and the window is
dropped, never retried into a growing backlog.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("metrics")

NAMESPACE = os.getenv("METRICS_NAMESPACE", "Mycelium/Api")
SERVICE = os.getenv("METRICS_SERVICE", "mycelium-api")


class Window:
    """Counters for one flush interval."""

    __slots__ = (
        "count",
        "errors",
        "client_errors",
        "duration_sum",
        "duration_min",
        "duration_max",
    )

    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.client_errors = 0
        self.duration_sum = 0.0
        self.duration_min = float("inf")
        self.duration_max = 0.0


class MetricsPublisher:
    def __init__(
        self,
        client: Any,
        instance_id: str,
        flush_seconds: float = 5.0,
        namespace: str = NAMESPACE,
        service: str = SERVICE,
        clock=time.time,
    ) -> None:
        self.client = client
        self.instance_id = instance_id
        self.flush_seconds = flush_seconds
        self.namespace = namespace
        self.service = service
        self.clock = clock
        self._lock = threading.Lock()
        self._window = Window()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- recording (hot path) ---------------------------------------------------

    def record(self, status: int, duration_ms: float) -> None:
        with self._lock:
            w = self._window
            w.count += 1
            if status >= 500:
                w.errors += 1
            elif status >= 400:
                w.client_errors += 1
            w.duration_sum += duration_ms
            w.duration_min = min(w.duration_min, duration_ms)
            w.duration_max = max(w.duration_max, duration_ms)

    # ---- publishing ---------------------------------------------------------------

    def _take(self) -> Window | None:
        with self._lock:
            window, self._window = self._window, Window()
        return window if window.count else None

    def build(self, window: Window, timestamp: datetime) -> list[dict]:
        """MetricData for one window, for both dimension sets."""
        dimension_sets = [
            # "Scope=all" keeps the system-wide series apart from the per-instance one:
            # a query by Service alone could also match the per-instance data points
            [
                {"Name": "Service", "Value": self.service},
                {"Name": "Scope", "Value": "all"},
            ],
            [
                {"Name": "Service", "Value": self.service},
                {"Name": "InstanceId", "Value": self.instance_id},
            ],
        ]
        data: list[dict] = []
        for dimensions in dimension_sets:
            common = {
                "Dimensions": dimensions,
                "Timestamp": timestamp,
                "StorageResolution": 1,
            }
            data += [
                {
                    **common,
                    "MetricName": "RequestCount",
                    "Value": window.count,
                    "Unit": "Count",
                },
                {
                    **common,
                    "MetricName": "Errors",
                    "Value": window.errors,
                    "Unit": "Count",
                },
                {
                    **common,
                    "MetricName": "ClientErrors",
                    "Value": window.client_errors,
                    "Unit": "Count",
                },
                {
                    **common,
                    "MetricName": "Duration",
                    "StatisticValues": {
                        "SampleCount": window.count,
                        "Sum": window.duration_sum,
                        "Minimum": window.duration_min,
                        "Maximum": window.duration_max,
                    },
                    "Unit": "Milliseconds",
                },
            ]
        return data

    def flush(self) -> bool:
        """Publish the current window. Returns True if something was sent."""
        window = self._take()
        if window is None:
            return False
        data = self.build(window, datetime.fromtimestamp(self.clock(), UTC))
        try:
            self.client.put_metric_data(Namespace=self.namespace, MetricData=data)
        except Exception:
            logger.warning(
                "PutMetricData failed; window of %d requests dropped",
                window.count,
                exc_info=True,
            )
            return False
        return True

    # ---- background thread ----------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="metrics", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.flush_seconds):
            self.flush()

    def stop(self) -> None:
        self._stop.set()
        self.flush()  # do not lose the last window on shutdown

    @classmethod
    def from_env(cls, instance_id: str) -> MetricsPublisher | None:
        """Enabled with METRICS_ENABLED=1; talks to AWS_ENDPOINT_URL (LocalStack)."""
        if os.getenv("METRICS_ENABLED") != "1":
            return None
        import boto3

        endpoint = os.getenv("AWS_ENDPOINT_URL")
        client = boto3.client(
            "cloudwatch", **({"endpoint_url": endpoint} if endpoint else {})
        )
        return cls(client, instance_id, float(os.getenv("METRICS_FLUSH_SECONDS", "5")))
