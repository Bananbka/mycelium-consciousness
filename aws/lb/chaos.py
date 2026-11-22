"""Fault injection for the monitoring experiments (lab 10).

Lets an operator make one instance slow and/or failing at runtime, so that the
metrics and the CloudWatch alarms have something real to react to:

    POST   /admin/chaos  {"error_rate": 0.4, "latency_ms": 300}
    GET    /admin/chaos
    DELETE /admin/chaos

Only compiled in when ``CHAOS_ENABLED=1`` (the lab compose file sets it); a
production deployment would simply not set it.
"""

from __future__ import annotations

import random
import threading


class Chaos:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._lock = threading.Lock()
        self._rng = rng or random.Random()
        self.error_rate = 0.0
        self.latency_ms = 0.0

    def configure(self, body: dict) -> dict:
        """Validate and apply; raises ValueError with a message for the client."""
        rate = body.get("error_rate", 0.0)
        latency = body.get("latency_ms", 0.0)
        for name, value in (("error_rate", rate), ("latency_ms", latency)):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number")
        if not 0.0 <= rate <= 1.0:
            raise ValueError("error_rate must be between 0 and 1")
        if not 0.0 <= latency <= 10_000:
            raise ValueError("latency_ms must be between 0 and 10000")
        with self._lock:
            self.error_rate, self.latency_ms = float(rate), float(latency)
        return self.state()

    def reset(self) -> dict:
        return self.configure({})

    def state(self) -> dict:
        with self._lock:
            return {"error_rate": self.error_rate, "latency_ms": self.latency_ms}

    def decide(self) -> tuple[float, bool]:
        """(seconds to wait, whether this request must fail)."""
        with self._lock:
            return self.latency_ms / 1000, self._rng.random() < self.error_rate
