"""Markdown table of the lab 10 failure scenarios (l10_fail_*.json results)."""

from __future__ import annotations

import json
import os
from pathlib import Path

SUF = os.getenv("SUFFIX", "")
RES = Path(__file__).resolve().parent.parent / "load-tests" / "results"
LABEL = {
    "instance": "one api instance stopped",
    "redis": "Redis stopped",
    "replica": "read replica stopped",
    "shard": "shard-b stopped",
    "primary": "control-plane primary stopped",
}


def main() -> None:
    cols = ["Failure", "Scenario", "Requests", "Failed", "Error %", "p50 ms"]
    cols += ["p95 ms", "p99 ms"]
    print("| " + " | ".join(cols) + " |")
    print("|" + " --- |" * len(cols))
    for what, label in LABEL.items():
        for scen, name in (("A", "A read"), ("B", "B write"), ("C", "C workflow")):
            f = RES / f"l10_fail_{what}_{scen}{SUF}.json"
            if not f.exists():
                continue
            m = json.loads(f.read_text())["metrics"]
            dur = m.get("http_req_duration{phase:main}", m["http_req_duration"])
            reqs = m.get("http_reqs{phase:main}", m["http_reqs"])
            failed = m.get("http_req_failed{phase:main}", m["http_req_failed"])
            err = 100 * failed.get("value", failed.get("rate", 0))
            count = reqs["count"]
            print(
                f"| {label} | {name} | {count} | {round(count * err / 100)} "
                f"| {err:.1f} | {dur['med']:.0f} | {dur['p(95)']:.0f} "
                f"| {dur['p(99)']:.0f} |"
            )


if __name__ == "__main__":
    main()
