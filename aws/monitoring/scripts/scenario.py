# ruff: noqa: E501
"""Controlled load + fault injection against the load-balanced application (lab 10).

Runs four phases against nginx (``--url``), while injecting faults directly into the
three instances through ``/admin/chaos``, and records the CloudWatch alarm states
every few seconds. The timeline is saved so ``report.py`` can line the metrics up
with what was done to the system.

    baseline   normal traffic
    errors     every instance answers a fraction of requests with 500
    latency    every instance adds artificial delay
    recovery   faults removed, traffic back to normal

    uv run --with boto3 python aws/monitoring/scripts/scenario.py
    uv run --with boto3 python aws/monitoring/scripts/scenario.py --phase-seconds 20  # quick
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.request
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lb" / "scripts"))
from lbtool import CLONE, Client, seed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALARMS = ["mycelium-errors-high", "mycelium-latency-high"]
INSTANCE_PORTS = [8081, 8082, 8083]


def chaos(port: int, settings: dict | None) -> None:
    """Set (or, with None, clear) fault injection on one instance."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/chaos",
        data=json.dumps(settings).encode() if settings is not None else None,
        method="POST" if settings is not None else "DELETE",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=5).read()


def run_phase(name, seconds, settings, url, concurrency, cw, log) -> dict:
    for port in INSTANCE_PORTS:
        chaos(port, settings)
    start = time.time()
    stop = start + seconds
    counts = {"ok": 0, "5xx": 0, "other": 0}
    lock = threading.Lock()

    def worker() -> None:
        client = Client(url)
        rng = random.Random()
        while time.time() < stop:
            if rng.random() < 0.85:
                status, _, _ = client.request("GET", f"/clones/{CLONE}")
            else:
                body = {"content": "controlled load frame"}
                status, _, _ = client.request("POST", f"/clones/{CLONE}/memories", body)
            with lock:
                if status in (200, 202):
                    counts["ok"] += 1
                elif status >= 500 or status == 0:
                    counts["5xx"] += 1
                else:
                    counts["other"] += 1

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    last = {}
    while time.time() < stop:
        time.sleep(5)
        states = {
            a["AlarmName"]: a["StateValue"]
            for a in cw.describe_alarms(AlarmNames=ALARMS)["MetricAlarms"]
        }
        for alarm, state in states.items():
            if last.get(alarm) != state:
                log.append({"t": time.time(), "alarm": alarm, "state": state})
                print(f"    [{time.time() - start:5.0f}s] {alarm}: {state}")
        last = states
    for t in threads:
        t.join()
    print(f"  {name:<9} done: {counts}")
    return {"name": name, "start": start, "end": time.time(), "chaos": settings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--endpoint", default="http://localhost:4566")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--phase-seconds", type=int, default=60)
    parser.add_argument("--baseline-seconds", type=int, default=45)
    parser.add_argument("--recovery-seconds", type=int, default=120)
    parser.add_argument("--error-rate", type=float, default=0.5)
    parser.add_argument("--latency-ms", type=float, default=400)
    args = parser.parse_args()

    cw = boto3.client(
        "cloudwatch",
        endpoint_url=args.endpoint,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    states = cw.describe_alarms(AlarmNames=ALARMS)["MetricAlarms"]
    if len(states) != len(ALARMS):
        raise SystemExit(
            "alarms are missing: run .\\aws\\monitoring\\scripts\\alarms.ps1"
        )
    seed(args.url)

    plan = [
        ("baseline", args.baseline_seconds, None),
        ("errors", args.phase_seconds, {"error_rate": args.error_rate}),
        ("latency", args.phase_seconds, {"latency_ms": args.latency_ms}),
        ("recovery", args.recovery_seconds, None),
    ]
    alarm_log: list[dict] = []
    phases = []
    started = time.time()
    print(f"scenario against {args.url}, {args.concurrency} workers")
    try:
        for name, seconds, settings in plan:
            print(f"\n== {name} ({seconds}s) chaos={settings}")
            phases.append(
                run_phase(
                    name, seconds, settings, args.url, args.concurrency, cw, alarm_log
                )
            )
    finally:
        for port in INSTANCE_PORTS:
            chaos(port, None)
    print("\nwaiting for the last metrics flush ...")
    time.sleep(8)

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "scenario.json").write_text(
        json.dumps(
            {
                "start": started,
                "end": time.time(),
                "phases": phases,
                "alarm_log": alarm_log,
            },
            indent=2,
        )
    )
    print(f"saved {out / 'scenario.json'}  ->  now run report.py")


if __name__ == "__main__":
    main()
