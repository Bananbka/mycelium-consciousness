# ruff: noqa: E501
"""Lab 10 baseline-vs-final table (load-tests/results/l10_<tag>_<scen>_<vus>.json).

python profiling/final_table.py base5 final
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "load-tests" / "results"
LEVELS = (10, 50, 100, 200)
NAMES = {"A": "A read", "B": "B write", "C": "C workflow"}


def load(tag: str, scen: str, vus: int) -> dict:
    m = json.loads((RES / f"l10_{tag}_{scen}_{vus}.json").read_text())["metrics"]
    dur = m.get("http_req_duration{phase:main}", m["http_req_duration"])
    reqs = m.get("http_reqs{phase:main}", m["http_reqs"])
    failed = m.get("http_req_failed{phase:main}", m["http_req_failed"])
    return {
        "rps": reqs["rate"],
        "p50": dur["med"],
        "p95": dur["p(95)"],
        "p99": dur["p(99)"],
        "err": 100 * failed.get("value", failed.get("rate", 0)),
    }


def delta(a: float, b: float, invert: bool = False) -> str:
    change = 100 * (b - a) / a
    return f"{change:+.0f}%"


def main(base: str, final: str) -> None:
    print("| Metric | Scenario | Lab 5 baseline | Final | Change |")
    print("| --- | --- | --- | --- | --- |")
    for scen, name in NAMES.items():
        b = {v: load(base, scen, v) for v in LEVELS}
        f = {v: load(final, scen, v) for v in LEVELS}
        plateau = (50, 100, 200)
        brps = statistics.fmean(b[v]["rps"] for v in plateau)
        frps = statistics.fmean(f[v]["rps"] for v in plateau)
        print(
            f"| Stable throughput (mean RPS, 50-200 VUs) | {name} | {brps:.0f} | {frps:.0f} | {delta(brps, frps)} |"
        )
        for key, label in (("p50", "p50"), ("p95", "p95"), ("p99", "p99 tail")):
            print(
                f"| {label} latency @100 VUs, ms | {name} | {b[100][key]:.0f} | "
                f"{f[100][key]:.0f} | {delta(b[100][key], f[100][key])} |"
            )
        print(
            f"| p99 @200 VUs (saturated), ms | {name} | {b[200]['p99']:.0f} | "
            f"{f[200]['p99']:.0f} | {delta(b[200]['p99'], f[200]['p99'])} |"
        )
        print(
            f"| Error rate @200 VUs, % | {name} | {b[200]['err']:.2f} | {f[200]['err']:.2f} | - |"
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
