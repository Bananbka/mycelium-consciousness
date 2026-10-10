"""Lab 8 experiments: stampede, avalanche and penetration against /fleet.

    export ADMIN_EMAIL=... ADMIN_PASSWORD=...
    uv run --with httpx python tools/stampede.py seed
    uv run --with httpx python tools/stampede.py stampede --mode none --vus 500
    uv run --with httpx python tools/stampede.py avalanche --label jitter0
    uv run --with httpx python tools/stampede.py penetration --guard none

The API must run with ALLOW_PROTECTION_OVERRIDE=1 so a request can choose its
protection mode. "DB computations" are counted by the application
(``GET /admin/cache-metrics``): one count = one execution of the expensive
query against the shards, however many requests were served.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import sys
import time

import httpx
from shard_load import (
    CpuSampler,
    admin_api,
    bearer,
    make_clones,
    pct,
    seed_backups,
    shard_of,
)


async def fleet_metrics(api, admin) -> dict:
    r = await _retry(lambda: api.http.get("/admin/cache-metrics", headers=admin))
    return r.json()


async def _retry(call, attempts: int = 30):
    """A freshly recreated api may answer 502 for a few seconds; wait it out."""
    for _ in range(attempts):
        r = await call()
        if r.status_code != 502 and r.status_code != 503:
            r.raise_for_status()
            return r
        await asyncio.sleep(1)
    r.raise_for_status()
    return r


async def reset(api, admin) -> None:
    await _retry(lambda: api.http.post("/admin/cache-metrics/reset", headers=admin))
    await _retry(lambda: api.http.post("/admin/cache/flush", headers=admin))


async def cmd_seed(args) -> None:
    api, admin = await admin_api(args.base)
    clones = await make_clones(api, f"{args.prefix}-lb", args.clones)
    placement: dict[str, list[int]] = {}
    for _, cid in clones:
        placement.setdefault(await shard_of(api, admin, cid), []).append(cid)
    seed_backups(placement, args.backups)
    print(
        json.dumps(
            {
                "clones": len(clones),
                "backups_each": args.backups,
                "per_shard": {s: len(v) for s, v in placement.items()},
            }
        )
    )


async def _token(api) -> str:
    token, _ = (await make_clones(api, "st8-reader", 1))[0]
    return token


async def cmd_stampede(args) -> None:
    """N simultaneous requests for one key that has just been force-expired."""
    api, admin = await admin_api(args.base)
    token = await _token(api)
    await reset(api, admin)

    headers = {**bearer(token), "X-Cache-Protection": args.mode}
    limits = httpx.Limits(
        max_connections=args.vus + 10, max_keepalive_connections=args.vus + 10
    )
    latencies: list[float] = []
    states: collections.Counter = collections.Counter()
    errors = 0
    failures: collections.Counter = collections.Counter()
    gate = asyncio.Event()

    async with httpx.AsyncClient(
        base_url=args.base, timeout=120, limits=limits
    ) as http:
        # open every connection first so the burst is a burst of *requests*
        await asyncio.gather(*(http.get("/health") for _ in range(args.vus)))
        # Warm the API's database/Redis pools. Against freshly started processes
        # a 500-request burst measures connection setup (SCRAM handshakes, new
        # sockets), not the cache - see "cold start" in the README.
        for _ in range(3):
            await asyncio.gather(
                *(http.get("/profiles/me", headers=bearer(token)) for _ in range(120)),
                return_exceptions=True,
            )
        await asyncio.sleep(3)
        await reset(api, admin)

        async def one() -> None:
            nonlocal errors
            await gate.wait()
            t0 = time.perf_counter()
            try:
                r = await http.get(
                    f"/fleet/leaderboard?limit={args.limit}", headers=headers
                )
                latencies.append((time.perf_counter() - t0) * 1000)
                states[r.headers.get("x-cache", f"HTTP{r.status_code}")] += (
                    r.status_code == 200
                )
                if r.status_code != 200:
                    errors += 1
                    failures[f"HTTP {r.status_code}"] += 1
            except Exception as exc:
                errors += 1
                failures[type(exc).__name__] += 1

        tasks = [asyncio.create_task(one()) for _ in range(args.vus)]
        await asyncio.sleep(0.5)
        with CpuSampler() as cpu:
            started = time.perf_counter()
            gate.set()
            await asyncio.gather(*tasks)
            wall = time.perf_counter() - started
            await asyncio.sleep(2.5)  # let docker stats see the tail

    counters = (await fleet_metrics(api, admin))["leaderboard"]
    print(
        json.dumps(
            {
                "label": f"{args.mode}",
                "vus": args.vus,
                "db_computations": counters.get("computes", 0),
                "lock_waits": counters.get("lock_waits", 0),
                "coalesced": counters.get("coalesced", 0),
                "requests_ok": len(latencies) - errors,
                "errors": errors,
                "error_kinds": dict(failures),
                "error_rate_pct": round(100 * errors / max(1, args.vus), 2),
                "wall_s": round(wall, 2),
                "rps": round(args.vus / wall, 1),
                "p50_ms": round(pct(latencies, 0.5)),
                "p95_ms": round(pct(latencies, 0.95)),
                "p99_ms": round(pct(latencies, 0.99)),
                "states": dict(states),
                "db_cpu_peak_pct": {
                    s: round(max(v), 1) for s, v in cpu.samples.items()
                },
            }
        )
    )


async def cmd_avalanche(args) -> None:
    """K keys written together, same TTL: when do they come back, and how hard?"""
    api, admin = await admin_api(args.base)
    token = await _token(api)
    await reset(api, admin)
    headers = {**bearer(token), "X-Cache-Protection": args.mode}
    keys = list(range(1, args.keys + 1))
    series: list[tuple[float, int]] = []
    latencies: list[tuple[float, float]] = []

    async with httpx.AsyncClient(base_url=args.base, timeout=60) as http:
        await asyncio.gather(
            *(http.get(f"/fleet/leaderboard?limit={k}", headers=headers) for k in keys)
        )  # warm every key at (almost) the same instant
        base = (await fleet_metrics(api, admin))["leaderboard"].get("computes", 0)
        t0 = time.perf_counter()
        stop = t0 + args.duration

        async def traffic() -> None:
            sem = asyncio.Semaphore(args.concurrency)

            async def hit() -> None:
                async with sem:
                    s = time.perf_counter()
                    await http.get(
                        f"/fleet/leaderboard?limit={random.choice(keys)}",
                        headers=headers,
                    )
                    latencies.append((s - t0, (time.perf_counter() - s) * 1000))

            pending: set[asyncio.Task] = set()
            while time.perf_counter() < stop:
                pending.add(asyncio.create_task(hit()))
                pending = {t for t in pending if not t.done()}
                await asyncio.sleep(1 / args.rps)
            await asyncio.gather(*pending)

        async def sampler() -> None:
            while time.perf_counter() < stop:
                m = (await fleet_metrics(api, admin))["leaderboard"].get("computes", 0)
                series.append((time.perf_counter() - t0, m - base))
                await asyncio.sleep(0.5)

        await asyncio.gather(traffic(), sampler())

    # computations per whole second
    per_second = collections.Counter()
    prev = 0
    for t, total in series:
        per_second[int(t)] += total - prev
        prev = total
    lat_by_second: dict[int, list[float]] = collections.defaultdict(list)
    for t, ms in latencies:
        lat_by_second[int(t)].append(ms)
    # ignore the first seconds: that is the initial warm-up of every key
    steady = {s: n for s, n in per_second.items() if s >= 2}
    peak = max(steady.values()) if steady else 0
    print(
        json.dumps(
            {
                "label": args.label,
                "mode": args.mode,
                "keys": args.keys,
                "ttl_s": args.ttl,
                "computes_total": prev,
                "computes_peak_per_s": peak,
                "peak_second": max(steady, key=steady.get) if steady else None,
                "p99_peak_second_ms": round(
                    pct(lat_by_second[max(steady, key=steady.get)], 0.99)
                )
                if steady
                else None,
                "computes_per_second": {
                    str(s): per_second[s] for s in sorted(per_second)
                },
            }
        )
    )


async def cmd_penetration(args) -> None:
    """A flood of lookups for ids that do not exist."""
    api, admin = await admin_api(args.base)
    token = await _token(api)
    await reset(api, admin)
    headers = {**bearer(token), "X-Penetration-Guard": args.guard}
    lat: list[float] = []
    codes: collections.Counter = collections.Counter()
    sem = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(max_connections=args.concurrency + 10)

    def target() -> int:
        if args.pattern == "same":
            return 9_999_999
        return random.randint(50_000_000, 90_000_000)  # distinct missing ids

    async with httpx.AsyncClient(base_url=args.base, timeout=60, limits=limits) as http:

        async def one() -> None:
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await http.get(f"/fleet/clones/{target()}", headers=headers)
                except Exception as exc:  # noqa: BLE001 - a timeout is a result here
                    lat.append((time.perf_counter() - t0) * 1000)
                    codes[(0, type(exc).__name__)] += 1
                    return
                lat.append((time.perf_counter() - t0) * 1000)
                codes[(r.status_code, r.headers.get("x-cache", "-"))] += 1

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(args.requests)))
        wall = time.perf_counter() - started

    counters = (await fleet_metrics(api, admin))["fleet-clone"]
    print(
        json.dumps(
            {
                "label": f"{args.guard}/{args.pattern}",
                "requests": args.requests,
                "db_computations": counters.get("computes", 0),
                "bloom_rejects": counters.get("bloom_rejects", 0),
                "rps": round(args.requests / wall, 1),
                "p50_ms": round(pct(lat, 0.5)),
                "p95_ms": round(pct(lat, 0.95)),
                "p99_ms": round(pct(lat, 0.99)),
                "responses": {f"{c}/{x}": n for (c, x), n in codes.items()},
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["seed", "stampede", "avalanche", "penetration"]
    )
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--prefix", default="st8")
    parser.add_argument("--label", default="run")
    parser.add_argument("--clones", type=int, default=60)
    parser.add_argument("--backups", type=int, default=1500, help="rows per clone")
    parser.add_argument("--mode", default="none", choices=["none", "lock", "swr"])
    parser.add_argument("--vus", type=int, default=500)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--keys", type=int, default=60)
    parser.add_argument(
        "--ttl", type=int, default=10, help="LEADERBOARD_TTL_SECONDS in use"
    )
    parser.add_argument("--duration", type=int, default=28)
    parser.add_argument("--rps", type=float, default=80)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument(
        "--guard", default="none", choices=["none", "negative", "bloom", "both"]
    )
    parser.add_argument("--pattern", default="same", choices=["same", "distinct"])
    parser.add_argument("--requests", type=int, default=3000)
    args = parser.parse_args()
    for var in ("ADMIN_EMAIL", "ADMIN_PASSWORD"):
        if var not in os.environ:
            sys.exit(f"{var} is required")
    asyncio.run(
        {
            "seed": cmd_seed,
            "stampede": cmd_stampede,
            "avalanche": cmd_avalanche,
            "penetration": cmd_penetration,
        }[args.command](args)
    )


if __name__ == "__main__":
    main()
