"""Before/after tables from k6 exports (``load-tests/results/l9_<tag>_*.json``).

python profiling/compare.py base opt            # scenario x VUs, base vs opt
python profiling/compare.py base join opt       # every step in one table
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "load-tests" / "results"


def load(tag: str, scen: str, vus: int) -> dict | None:
    f = RES / f"l9_{tag}_{scen}_{vus}.json"
    if not f.exists():
        return None
    m = json.loads(f.read_text())["metrics"]
    dur = m.get("http_req_duration{phase:main}", m["http_req_duration"])
    reqs = m.get("http_reqs{phase:main}", m["http_reqs"])
    failed = m.get("http_req_failed{phase:main}", m.get("http_req_failed", {}))
    cpu = {}
    stats = RES / f"l9_{tag}_{scen}_{vus}.stats"
    if stats.exists():
        for line in stats.read_text().splitlines():
            mm = re.match(r"(\S+) cpu=([\d.]+)%", line)
            if mm:
                cpu[mm.group(1)] = float(mm.group(2))
    return {
        "rps": reqs["rate"],
        "p50": dur["med"],
        "p95": dur["p(95)"],
        "p99": dur["p(99)"],
        "err": 100 * failed.get("value", failed.get("rate", 0)),
        "api_cpu": sum(v for k, v in cpu.items() if "-api-" in k),
        "pg_cpu": sum(
            v for k, v in cpu.items() if "postgres" in k and "replica" not in k
        )
        + sum(v for k, v in cpu.items() if "shard" in k),
    }


def delta(a: float, b: float) -> str:
    return f"{100 * (b - a) / a:+.0f}%" if a else "n/a"


def main(tags: list[str]) -> None:
    print(
        "| Scenario | VUs | "
        + " | ".join(f"{t} RPS" for t in tags)
        + " | dRPS | "
        + " | ".join(f"{t} p95" for t in tags)
        + " | dp95 | "
        + " | ".join(f"{t} p99" for t in tags)
        + " | err % | "
        + " | ".join(f"{t} api CPU" for t in tags)
        + " |"
    )
    print("|" + " --- |" * (4 + 3 * len(tags) + len(tags) + 1))
    for scen in "ABC":
        for vus in (10, 50, 100):
            rows = [load(t, scen, vus) for t in tags]
            if not all(rows):
                continue
            first, last = rows[0], rows[-1]
            cells = [scen, str(vus)]
            cells += [f"{r['rps']:.0f}" for r in rows] + [
                delta(first["rps"], last["rps"])
            ]
            cells += [f"{r['p95']:.0f}" for r in rows] + [
                delta(first["p95"], last["p95"])
            ]
            cells += [f"{r['p99']:.0f}" for r in rows]
            cells += [f"{max(r['err'] for r in rows):.2f}"]
            cells += [f"{r['api_cpu']:.0f}%" for r in rows]
            print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
