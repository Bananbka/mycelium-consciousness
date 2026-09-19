"""Closed-loop benchmark through the load balancer (lab 3).

Registers a throwaway clone, then hammers one endpoint with N workers and
reports throughput, latency percentiles, errors, and how requests were spread
over `X-Instance-ID`.

    uv run --with httpx python tools/lb_bench.py --duration 20 --concurrency 30
    uv run --with httpx python tools/lb_bench.py --path /memories/write --method POST
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from collections import Counter


def pct(sorted_values: list[float], q: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * q))]


async def run(args: argparse.Namespace) -> None:
    import httpx

    limits = httpx.Limits(max_connections=args.concurrency * 2)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=15, limits=limits
    ) as client:
        tag = uuid.uuid4().hex[:8]
        if args.email:
            creds = {"email": args.email, "password": args.password}
        else:
            creds = {
                "email": f"bench-{tag}@example.com",
                "password": uuid.uuid4().hex,
            }
            await client.post(
                "/auth/register", json={**creds, "designation": f"b-{tag}"}
            )
        login = await client.post("/auth/login", json=creds)
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        if args.no_cache:
            headers["Cache-Control"] = "no-cache"

        latencies: list[float] = []
        by_instance: Counter[str] = Counter()
        codes: Counter[int | str] = Counter()
        cache_states: Counter[str] = Counter()

        async def one() -> None:
            started = time.perf_counter()
            try:
                if args.method == "POST":
                    r = await client.post(
                        args.path, headers=headers, json={"content": "x" * 64}
                    )
                else:
                    r = await client.get(args.path, headers=headers)
            except httpx.HTTPError as exc:
                codes[type(exc).__name__] += 1
                return
            latencies.append((time.perf_counter() - started) * 1000)
            codes[r.status_code] += 1
            by_instance[r.headers.get("x-instance-id", "?")] += 1
            cache_states[r.headers.get("x-cache", "-")] += 1

        for _ in range(args.warmup):
            await one()
        latencies.clear()
        by_instance.clear()
        codes.clear()
        cache_states.clear()

        stop_at = time.monotonic() + args.duration

        async def worker() -> None:
            while time.monotonic() < stop_at:
                await one()

        began = time.monotonic()
        await asyncio.gather(*[worker() for _ in range(args.concurrency)])
        elapsed = time.monotonic() - began

    total = sum(codes.values())
    bad = sum(
        v for k, v in codes.items() if not (isinstance(k, int) and 200 <= k < 300)
    )
    print(f"requests    : {total} in {elapsed:.1f}s  ({total / elapsed:.1f} rps)")
    print(
        f"errors      : {bad} ({100 * bad / max(total, 1):.2f}%)  codes={dict(codes)}"
    )
    if latencies:
        s = sorted(latencies)
        print(
            f"latency ms  : avg {sum(s) / len(s):.1f}  p50 {pct(s, 0.5):.1f}  "
            f"p95 {pct(s, 0.95):.1f}  p99 {pct(s, 0.99):.1f}"
        )
    print(f"x-cache     : {dict(cache_states)}")
    served = sum(by_instance.values()) or 1
    for inst, n in sorted(by_instance.items()):
        print(f"instance {inst:>14}: {n:6d} ({100 * n / served:.1f}%)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://localhost")
    p.add_argument("--path", default="/profiles/me")
    p.add_argument("--method", choices=["GET", "POST"], default="GET")
    p.add_argument("--concurrency", type=int, default=30)
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--email", help="reuse this account instead of registering")
    p.add_argument("--password")
    p.add_argument(
        "--no-cache", action="store_true", help="send Cache-Control: no-cache"
    )
    p.add_argument("--warmup", type=int, default=50)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
