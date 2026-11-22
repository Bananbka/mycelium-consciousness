# ruff: noqa: E501
"""Load-balancer experiments for lab 9 (standard library only).

    python aws/lb/scripts/lbtool.py distribution --url http://localhost:8080 --count 60
    python aws/lb/scripts/lbtool.py load         --url http://localhost:8080 --duration 15
    python aws/lb/scripts/lbtool.py outage       --url http://localhost:8080 --container lb-app2
    python aws/lb/scripts/lbtool.py outage       --url http://localhost:8081 --container lb-app1

* ``distribution`` - N sequential requests, counted per ``X-Instance-ID``.
* ``load``         - concurrent workers for a fixed time: RPS, latency percentiles,
                     errors, per-instance counts.
* ``outage``       - the same load while a container is stopped (1/3 of the time)
                     and started again (2/3); prints a per-second timeline, so you
                     see whether clients noticed. Run it against nginx *and*
                     directly against the instance to compare with/without
                     balancing.
"""

from __future__ import annotations

import argparse
import collections
import http.client
import json
import statistics
import subprocess
import threading
import time
import urllib.parse

CLONE = "loadtest"


class Client:
    """One keep-alive connection per worker; reconnects after any failure."""

    def __init__(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.conn: http.client.HTTPConnection | None = None

    def request(self, method: str, path: str, body: dict | None = None):
        """Returns (status, instance, elapsed_ms); status 0 = no answer at all."""
        started = time.perf_counter()
        try:
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if data else {}
            self.conn.request(method, path, data, headers)
            response = self.conn.getresponse()
            response.read()
            instance = response.getheader("X-Instance-ID", "?")
            return response.status, instance, (time.perf_counter() - started) * 1000
        except (OSError, http.client.HTTPException):
            if self.conn is not None:
                self.conn.close()
            self.conn = None
            return 0, "-", (time.perf_counter() - started) * 1000


def seed(url: str) -> None:
    """Make sure the clone used by the load exists (409 = already there)."""
    status, _, _ = Client(url).request("POST", "/clones", {"designation": CLONE})
    if status not in (201, 409):
        raise SystemExit(f"cannot reach the API at {url} (status {status})")


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def cmd_distribution(args) -> None:
    client = Client(args.url)
    counts: collections.Counter = collections.Counter()
    for _ in range(args.count):
        status, instance, _ = client.request("GET", "/health")
        counts[instance if status == 200 else f"error({status})"] += 1
    print(f"{args.count} requests to {args.url}/health")
    for name, n in sorted(counts.items()):
        print(
            f"  {name:<12} {n:>5}  {100 * n / args.count:5.1f}%  {'#' * (n * 40 // args.count)}"
        )


def _run_load(url: str, duration: float, concurrency: int, on_second=None):
    """Closed-loop load; returns (records, timeline) where a record is
    (t_offset, status, instance, ms)."""
    records: list[tuple[float, int, str, float]] = []
    lock = threading.Lock()
    start = time.perf_counter()
    stop = start + duration

    def worker() -> None:
        client = Client(url)
        local = []
        while time.perf_counter() < stop:
            status, instance, ms = client.request("GET", f"/clones/{CLONE}")
            local.append((time.perf_counter() - start, status, instance, ms))
            if status == 0:
                time.sleep(0.05)  # do not spin while the target is down
        with lock:
            records.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    if on_second:
        on_second(start, stop)
    for t in threads:
        t.join()
    return records, start


def cmd_load(args) -> None:
    seed(args.url)
    records, _ = _run_load(args.url, args.duration, args.concurrency)
    ok = [r for r in records if r[1] == 200]
    errors = len(records) - len(ok)
    latencies = [r[3] for r in ok]
    per_instance = collections.Counter(r[2] for r in ok)
    print(f"{args.url}  {args.concurrency} workers x {args.duration:.0f}s")
    print(f"  requests    {len(records)}  ({len(records) / args.duration:.0f} RPS)")
    print(f"  errors      {errors}  ({100 * errors / max(1, len(records)):.1f}%)")
    if latencies:
        print(
            f"  latency ms  p50 {statistics.median(latencies):.0f}  "
            f"p95 {pct(latencies, 0.95):.0f}  p99 {pct(latencies, 0.99):.0f}"
        )
    print(f"  by instance {dict(sorted(per_instance.items()))}")


def docker(*cmd: str) -> None:
    subprocess.run(["docker", *cmd], check=True, capture_output=True)


def cmd_outage(args) -> None:
    seed(args.url)
    events: list[tuple[float, str]] = []

    def chaos(start: float, stop: float) -> None:
        total = stop - start
        for when, action in ((total / 3, "stop"), (2 * total / 3, "start")):
            time.sleep(max(0, start + when - time.perf_counter()))
            docker(action, args.container)
            events.append(
                (time.perf_counter() - start, f"docker {action} {args.container}")
            )

    chaos_thread = threading.Thread(
        target=chaos, args=(time.perf_counter(), time.perf_counter() + args.duration)
    )
    chaos_thread.start()
    records, _ = _run_load(args.url, args.duration, args.concurrency)
    chaos_thread.join()

    print(f"{args.url}  while {args.container} is stopped and started again")
    for at, text in events:
        print(f"  t={at:5.1f}s  {text}")
    print("\n  sec   ok  errors  instances that answered")
    for second in range(int(args.duration)):
        window = [r for r in records if second <= r[0] < second + 1]
        ok = [r for r in window if r[1] == 200]
        names = collections.Counter(r[2] for r in ok)
        print(
            f"  {second:>3} {len(ok):>5} {len(window) - len(ok):>7}  {dict(sorted(names.items()))}"
        )
    errors = sum(1 for r in records if r[1] != 200)
    print(
        f"\n  total {len(records)} requests, {errors} failed ({100 * errors / max(1, len(records)):.1f}%)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("distribution", "load", "outage"):
        p = sub.add_parser(name)
        p.add_argument("--url", default="http://localhost:8080")
        if name == "distribution":
            p.add_argument("--count", type=int, default=60)
        else:
            p.add_argument(
                "--duration", type=float, default=30 if name == "outage" else 15
            )
            p.add_argument("--concurrency", type=int, default=10)
        if name == "outage":
            p.add_argument("--container", required=True)
    args = parser.parse_args()
    {"distribution": cmd_distribution, "load": cmd_load, "outage": cmd_outage}[
        args.command
    ](args)


if __name__ == "__main__":
    main()
