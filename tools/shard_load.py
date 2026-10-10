"""Lab 7 experiments: data distribution, hot shard, shard-local vs fan-out.

    uv run --with httpx python tools/shard_load.py distribution --clones 120
    uv run --with httpx python tools/shard_load.py hot --label modulo
    uv run --with httpx python tools/shard_load.py fanout --label sharded

Needs a running stack and an admin account (ADMIN_EMAIL / ADMIN_PASSWORD).
`COMPOSE` is the compose command used to reach the databases (default:
``docker compose -f infra/docker-compose.yaml``).

Seeding and measurement go through the public API plus one `psql` per shard, so
the numbers reflect what a client would see; per-node CPU comes from
``docker stats``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import uuid

import httpx

COMPOSE = os.getenv("COMPOSE", "docker compose -f infra/docker-compose.yaml")
DB_USER = os.getenv("POSTGRES_USER", "admin")
DB_NAME = os.getenv("POSTGRES_DB", "clone_memory_db")
SERVICE = {"a": "shard-a", "b": "shard-b", "default": "db"}
JSON = {"Content-Type": "application/json"}


# ---- helpers ---------------------------------------------------------------


def sh(command: str, stdin: str | None = None) -> str:
    # shell=True means cmd.exe on Windows, which would hand a *.sh wrapper to the
    # "open with" dialog instead of running it.
    if command.split(" ", 1)[0].endswith(".sh"):
        command = f"bash {command}"
    done = subprocess.run(
        command, shell=True, input=stdin, capture_output=True, text=True, check=False
    )
    if done.returncode:
        raise RuntimeError(f"{command!r} failed: {done.stderr.strip()}")
    return done.stdout


def psql(shard: str, sql: str) -> str:
    return sh(
        f"{COMPOSE} exec -T {SERVICE[shard]} psql -U {DB_USER} -d {DB_NAME} -tA",
        stdin=sql,
    )


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


class Api:
    def __init__(self, base: str) -> None:
        self.http = httpx.AsyncClient(base_url=base, timeout=60)

    async def login(self, email: str, password: str) -> str:
        r = await self.http.post(
            "/auth/login", json={"email": email, "password": password}
        )
        r.raise_for_status()
        return r.json()["access_token"]

    async def clone(
        self, email: str, password: str, designation: str
    ) -> tuple[str, int]:
        await self.http.post(
            "/auth/register",
            json={"email": email, "password": password, "designation": designation},
        )  # 201 new, 409 existing: both fine
        token = await self.login(email, password)
        me = await self.http.get("/profiles/me", headers=bearer(token))
        me.raise_for_status()
        return token, me.json()["id"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def admin_api(base: str) -> tuple[Api, dict[str, str]]:
    api = Api(base)
    token = await api.login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
    return api, bearer(token)


async def make_clones(api: Api, prefix: str, count: int) -> list[tuple[str, int]]:
    sem = asyncio.Semaphore(8)  # password hashing is CPU-bound on the API

    async def one(i: int):
        async with sem:
            return await api.clone(
                f"{prefix}-{i}@example.com", f"{prefix}-pass-{i}-Zx9", f"{prefix}-{i}"
            )

    return list(await asyncio.gather(*(one(i) for i in range(count))))


async def shard_of(api: Api, admin: dict, clone_id: int) -> str:
    r = await api.http.get(f"/admin/shards/locate/{clone_id}", headers=admin)
    r.raise_for_status()
    return r.json()["shard"]


def seed_backups(by_shard: dict[str, list[int]], per_clone: int) -> None:
    """Idempotently give each clone `per_clone` backup rows on its own shard."""
    for shard, ids in by_shard.items():
        if not ids:
            continue
        psql(
            shard,
            f"""
            INSERT INTO memory_backups
              (clone_id, period_start, period_end, entry_count, storage_key,
               subscription_tier)
            SELECT c, now() - (g || ' hours')::interval,
                   now() - (g || ' hours')::interval, 10,
                   'seed/' || c || '/' || g, 'free'
            FROM unnest(ARRAY[{",".join(map(str, ids))}]) c,
                 generate_series(1, {per_clone}) g
            WHERE NOT EXISTS (SELECT 1 FROM memory_backups b WHERE b.clone_id = c);
            """,
        )


class CpuSampler:
    """Samples `docker stats` for the database containers in the background."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _classify(name: str) -> str | None:
        if "shard-a" in name or "shard_a" in name:
            return "a"
        if "shard-b" in name or "shard_b" in name:
            return "b"
        if name.endswith("postgres") or name.endswith("-db-1"):
            return "default"
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            out = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.Name}} {{.CPUPerc}}",
                ],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for line in out.splitlines():
                name, _, cpu = line.partition(" ")
                shard = self._classify(name)
                if shard and cpu.endswith("%"):
                    self.samples.setdefault(shard, []).append(float(cpu[:-1]))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)

    def mean(self, shard: str) -> float:
        values = self.samples.get(shard, [])
        return statistics.fmean(values) if values else 0.0


def rows_read(shards: list[str]) -> dict[str, int]:
    return {
        s: int(
            psql(
                s,
                f"SELECT tup_returned + tup_fetched FROM pg_stat_database "
                f"WHERE datname = '{DB_NAME}'",
            ).strip()
            or 0
        )
        for s in shards
    }


async def distribution_snapshot(api: Api, admin: dict) -> list[dict]:
    r = await api.http.get("/admin/shards", headers=admin)
    r.raise_for_status()
    return r.json()["shards"]


# ---- commands --------------------------------------------------------------


async def cmd_distribution(args) -> None:
    """Dataset Distribution Baseline: clones, entries and write ops per shard."""
    api, admin = await admin_api(args.base)
    before = {s["name"]: s for s in await distribution_snapshot(api, admin)}

    clones = await make_clones(api, f"{args.prefix}-dist", args.clones)
    rng = random.Random(7)
    # A realistic skew: a few busy clones, a long tail of quiet ones (Zipf-ish).
    weights = [max(1, int(60 / (rank + 1) ** 0.9)) for rank in range(args.clones)]
    rng.shuffle(weights)

    expected = 0

    async def writes(token: str, count: int) -> None:
        for _ in range(count):
            r = await api.http.post(
                "/memories/write",
                json={"content": uuid.uuid4().hex},
                headers=bearer(token),
            )
            r.raise_for_status()

    await asyncio.gather(
        *(writes(token, w) for (token, _), w in zip(clones, weights, strict=True))
    )
    expected = sum(weights)

    deadline = time.time() + args.wait
    while time.time() < deadline:
        now = {s["name"]: s for s in await distribution_snapshot(api, admin)}
        landed = sum(
            now[n]["buffered_entries"] - before[n]["buffered_entries"] for n in now
        )
        if landed >= expected:
            break
        await asyncio.sleep(3)
    # Postgres publishes per-table counters from an idle backend lazily
    # (up to ~10 s), so wait before the closing snapshot.
    await asyncio.sleep(12)
    now = {s["name"]: s for s in await distribution_snapshot(api, admin)}

    names = list(now)
    d_clones = {n: now[n]["clones"] - before[n]["clones"] for n in names}
    d_entries = {
        n: now[n]["buffered_entries"] - before[n]["buffered_entries"] for n in names
    }
    d_writes = {n: now[n]["write_ops"] - before[n]["write_ops"] for n in names}
    tc, te, tw = (max(1, sum(d.values())) for d in (d_clones, d_entries, d_writes))

    strategy = (await api.http.get("/admin/shards", headers=admin)).json()["strategy"]
    print(f"strategy={strategy}  clones={args.clones}  writes={expected}")
    print(
        "| Shard | vbuckets | Clones | Clones % | Entries | Entries % "
        "| Write ops | Write ops % |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for n in names:
        print(
            f"| {n} | {now[n]['vbuckets']} | {d_clones[n]} "
            f"| {100 * d_clones[n] / tc:.1f} "
            f"| {d_entries[n]} | {100 * d_entries[n] / te:.1f} "
            f"| {d_writes[n]} | {100 * d_writes[n] / tw:.1f} |"
        )


async def cmd_hot(args) -> None:
    """Skewed read traffic from one big tenant against the current strategy."""
    api, admin = await admin_api(args.base)
    clones = await make_clones(api, f"{args.prefix}-hot", args.clones)

    # Tenant A owns the even ids (two onboarding pipelines alternate ids);
    # tenant B owns the odd ones and is nearly idle.
    big = [(t, c) for t, c in clones if c % 2 == 0]
    small = [(t, c) for t, c in clones if c % 2 == 1]
    placement: dict[str, list[int]] = {}
    for _, cid in clones:
        placement.setdefault(await shard_of(api, admin, cid), []).append(cid)
    seed_backups(placement, args.backups)

    shards = list(placement) or ["default"]
    share_of_big = {}
    for _, cid in big:
        s = await shard_of(api, admin, cid)
        share_of_big[s] = share_of_big.get(s, 0) + 1

    latencies: list[float] = []
    errors = 0
    stop_at = time.time() + args.duration

    async def worker(pool):
        nonlocal errors
        async with httpx.AsyncClient(base_url=args.base, timeout=60) as http:
            while time.time() < stop_at:
                token, _ = random.choice(pool)
                t0 = time.perf_counter()
                r = await http.get(
                    f"/memories/backups?limit={args.backups}",
                    headers={**bearer(token), "Cache-Control": "no-cache"},
                )
                latencies.append((time.perf_counter() - t0) * 1000)
                errors += r.status_code != 200

    base_rows = rows_read(shards)
    with CpuSampler() as cpu:
        # 90% of the connections serve the big tenant, 10% the small one.
        heavy = max(1, int(args.concurrency * 0.9))
        await asyncio.gather(
            *[worker(big) for _ in range(heavy)],
            *[worker(small) for _ in range(args.concurrency - heavy)],
        )
    await asyncio.sleep(12)  # per-table counters are published lazily
    rows = {s: v - base_rows[s] for s, v in rows_read(shards).items()}
    total = max(1, sum(rows.values()))

    result = {
        "label": args.label,
        "requests": len(latencies),
        "rps": round(len(latencies) / args.duration, 1),
        "errors": errors,
        "p50_ms": round(pct(latencies, 0.5)),
        "p95_ms": round(pct(latencies, 0.95)),
        "p99_ms": round(pct(latencies, 0.99)),
        "big_tenant_clones_per_shard": share_of_big,
        "rows_read_share_pct": {s: round(100 * v / total, 1) for s, v in rows.items()},
        "db_cpu_pct": {s: round(cpu.mean(s), 1) for s in shards},
    }
    print(json.dumps(result))


async def cmd_seed(args) -> None:
    """Give every load-test clone backups on the shard that currently owns it."""
    api, admin = await admin_api(args.base)
    r = await api.http.get("/admin/clones?limit=500", headers=admin)
    r.raise_for_status()
    ids = [p["id"] for p in r.json() if p["designation"].startswith(args.designation)]
    placement: dict[str, list[int]] = {}
    for cid in ids:
        placement.setdefault(await shard_of(api, admin, cid), []).append(cid)
    seed_backups(placement, args.backups)
    print(json.dumps({s: len(v) for s, v in placement.items()}))


async def cmd_fanout(args) -> None:
    """Shard-local list vs cross-shard scatter-gather, same load, same dataset."""
    api, admin = await admin_api(args.base)
    clones = await make_clones(api, f"{args.prefix}-fan", args.clones)
    placement: dict[str, list[int]] = {}
    for _, cid in clones:
        placement.setdefault(await shard_of(api, admin, cid), []).append(cid)
    seed_backups(placement, args.backups)

    async def run(label: str, make_request) -> dict:
        lat: list[float] = []
        errors = 0
        stop_at = time.time() + args.duration

        async def worker(i: int):
            nonlocal errors
            async with httpx.AsyncClient(base_url=args.base, timeout=60) as http:
                while time.time() < stop_at:
                    t0 = time.perf_counter()
                    r = await make_request(http, i)
                    lat.append((time.perf_counter() - t0) * 1000)
                    errors += r.status_code != 200

        await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
        return {
            "query": label,
            "rps": round(len(lat) / args.duration, 1),
            "p50_ms": round(pct(lat, 0.5)),
            "p95_ms": round(pct(lat, 0.95)),
            "p99_ms": round(pct(lat, 0.99)),
            "errors": errors,
        }

    async def local(http, i):
        token, _ = clones[i % len(clones)]
        return await http.get(
            f"/memories/backups?limit={args.limit}",
            headers={**bearer(token), "Cache-Control": "no-cache"},
        )

    async def cross(http, _i):
        return await http.get(f"/admin/backups?limit={args.limit}", headers=admin)

    out = []
    with CpuSampler() as cpu:
        out.append(await run("shard-local: GET /memories/backups", local))
        out.append(await run("cross-shard: GET /admin/backups", cross))
    probe = await api.http.get("/admin/backups?limit=1", headers=admin)
    print(
        json.dumps(
            {
                "label": args.label,
                "shards_queried": probe.headers.get("x-shards-queried"),
                "db_cpu_pct": {s: round(cpu.mean(s), 1) for s in cpu.samples},
                "results": out,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["distribution", "hot", "fanout", "seed"])
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--prefix", default="sh7")
    parser.add_argument("--label", default="run")
    parser.add_argument("--clones", type=int, default=40)
    parser.add_argument(
        "--backups", type=int, default=200, help="seeded backup rows per clone"
    )
    parser.add_argument("--designation", default="lt-clone-")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument(
        "--wait", type=int, default=150, help="seconds to wait for flush"
    )
    args = parser.parse_args()
    for var in ("ADMIN_EMAIL", "ADMIN_PASSWORD"):
        if var not in os.environ:
            sys.exit(f"{var} is required")
    commands = {
        "distribution": cmd_distribution,
        "hot": cmd_hot,
        "fanout": cmd_fanout,
        "seed": cmd_seed,
    }
    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
