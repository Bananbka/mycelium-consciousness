"""Give the 50 Lab 5 load-test accounts 50 backups each, on their own shard.

    ADMIN_EMAIL=... ADMIN_PASSWORD=... COMPOSE="..." \
        uv run --with httpx python profiling/seed_lt.py --base http://localhost:18080

Lab 5 seeded the single database; since lab 7 the rows have to live on the
shard that owns the clone, so the same dataset is rebuilt shard by shard.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from shard_load import Api, admin_api, bearer, seed_backups, shard_of  # noqa: E402


async def main(base: str, users: int, backups: int) -> None:
    api = Api(base)
    _, admin = await admin_api(base)
    placement: dict[str, list[int]] = {}
    for i in range(users):
        token = await api.login(f"lt-user-{i}@example.com", f"lt-pass-{i}-Zx9")
        me = await api.http.get("/profiles/me", headers=bearer(token))
        me.raise_for_status()
        cid = me.json()["id"]
        placement.setdefault(await shard_of(api, admin, cid), []).append(cid)
    seed_backups(placement, backups)
    print({s: len(v) for s, v in placement.items()}, "backups each:", backups)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost")
    p.add_argument("--users", type=int, default=50)
    p.add_argument("--backups", type=int, default=50)
    a = p.parse_args()
    asyncio.run(main(a.base, a.users, a.backups))
