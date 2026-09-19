"""Simulate an implant writing large volumes of noise, to load-test ingestion.

    uv run --with httpx python tools/load_generator.py \\
        --base-url http://localhost --email alpha@clones.example.com \\
        --password alphapass123 --rate 200 --duration 30 --concurrency 20
"""

from __future__ import annotations

import argparse
import asyncio
import random
import string
import sys
import time
from dataclasses import dataclass, field

NOISE_ALPHABET = string.ascii_letters + string.digits + "     "


def random_noise(min_size: int, max_size: int) -> str:
    size = random.randint(min_size, max_size)
    return "".join(random.choices(NOISE_ALPHABET, k=size))


@dataclass
class Stats:
    sent: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def report(self, elapsed: float) -> None:
        total = self.sent + self.failed
        print(f"\n{'-' * 50}")
        print(f"duration        : {elapsed:.2f}s")
        print(f"sent            : {self.sent}")
        print(f"failed          : {self.failed}")
        print(f"throughput      : {self.sent / elapsed:.1f} writes/s")
        if self.latencies_ms:
            sorted_lat = sorted(self.latencies_ms)
            p50 = sorted_lat[len(sorted_lat) // 2]
            p99 = sorted_lat[int(len(sorted_lat) * 0.99)]
            print(f"latency p50/p99 : {p50:.2f}ms / {p99:.2f}ms")
        if total == 0:
            print("nothing was sent -- check connectivity/credentials")


async def run(args: argparse.Namespace) -> None:
    import httpx

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as client:
        login = await client.post(
            "/auth/login",
            json={"email": args.email, "password": args.password},
        )
        if login.status_code != 200:
            print(f"login failed: {login.status_code} {login.text}", file=sys.stderr)
            raise SystemExit(1)
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        stats = Stats()
        stop_at = time.monotonic() + args.duration
        per_worker_interval = args.concurrency / args.rate if args.rate > 0 else 0

        async def worker() -> None:
            while time.monotonic() < stop_at:
                content = random_noise(args.min_size, args.max_size)
                started = time.perf_counter()
                try:
                    response = await client.post(
                        "/memories/write",
                        headers=headers,
                        json={"content": content},
                    )
                    stats.latencies_ms.append((time.perf_counter() - started) * 1000)
                    if response.status_code == 202:
                        stats.sent += 1
                    else:
                        stats.failed += 1
                except httpx.HTTPError:
                    stats.failed += 1

                if per_worker_interval:
                    await asyncio.sleep(per_worker_interval)

        start = time.monotonic()
        await asyncio.gather(*[worker() for _ in range(args.concurrency)])
        stats.report(time.monotonic() - start)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--rate", type=float, default=100, help="target writes/sec, aggregate"
    )
    parser.add_argument("--duration", type=float, default=30, help="seconds to run")
    parser.add_argument("--concurrency", type=int, default=10, help="parallel workers")
    parser.add_argument(
        "--min-size", type=int, default=20, help="min noise chars per write"
    )
    parser.add_argument(
        "--max-size", type=int, default=200, help="max noise chars per write"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
