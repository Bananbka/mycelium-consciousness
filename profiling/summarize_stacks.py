"""Aggregate a py-spy `raw` (collapsed-stack) file into a self/inclusive table.

    python profiling/summarize_stacks.py profiling/artifacts/b_baseline.txt.gz [N]

Frames are grouped by *where the time is spent*: inclusive share per function
(any stack that contains it) and per package bucket (sqlalchemy, asyncpg, ...).
"""

from __future__ import annotations

import collections
import gzip
import re
import sys

BUCKETS = [
    ("idle/event-loop select", r"select \(selectors|_run_once|run_forever"),
    ("jwt / crypto", r"\bjwt\b|jose|hmac|hashlib|hmac\.py"),
    (
        "pydantic / serialization",
        r"pydantic|jsonable_encoder|serialize_response|json/__init__|orjson",
    ),
    (
        "sqlalchemy orm",
        r"sqlalchemy/orm|sqlalchemy/sql|sqlalchemy/engine|sqlalchemy/ext",
    ),
    ("asyncpg / db driver", r"asyncpg|sqlalchemy/dialects"),
    ("redis client", r"redis/|redis\.asyncio"),
    ("starlette middleware", r"starlette/middleware|BaseHTTPMiddleware|base\.py"),
    ("fastapi routing/deps", r"fastapi/"),
    ("uvicorn / h11 / httptools", r"uvicorn|h11|httptools"),
]


def main(path: str, top: int = 18) -> None:
    total = 0
    inclusive: collections.Counter = collections.Counter()
    leaf: collections.Counter = collections.Counter()
    bucket: collections.Counter = collections.Counter()
    opener = gzip.open if path.endswith(".gz") else open
    for line in opener(path, "rt", encoding="utf-8"):
        stack, _, n = line.rstrip("\n").rpartition(" ")
        if not n.isdigit():
            continue
        n = int(n)
        frames = [re.sub(r":\d+\)", ")", f) for f in stack.split(";")]
        frames = [f for f in frames if not f.startswith(("process ", " spawn_main"))]
        if not frames:
            continue
        total += n
        leaf[frames[-1]] += n
        for f in set(frames):
            inclusive[f] += n
        seen = set()
        for f in frames:
            for name, rx in BUCKETS:
                if name not in seen and re.search(rx, f):
                    seen.add(name)
                    bucket[name] += n
    print(f"samples: {total}\n\n== self time (leaf frames) ==")
    for f, n in leaf.most_common(top):
        print(f"{100 * n / total:5.1f}%  {f}")
    print("\n== inclusive (functions in stack) ==")
    for f, n in inclusive.most_common(top + 10):
        print(f"{100 * n / total:5.1f}%  {f}")
    print("\n== buckets (inclusive) ==")
    for name, n in bucket.most_common():
        print(f"{100 * n / total:5.1f}%  {name}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 18)
