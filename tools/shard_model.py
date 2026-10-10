"""Lab 7 offline model: placement balance and the cost of resharding.

    uv run --with matplotlib python tools/shard_model.py [--out docs/media]

Uses the production router code (`shared.sharding`), so what is measured here is
what the services compute. Prints tables and writes two charts:

* shard-balance.png  - share of a skewed tenant's clones per shard, by strategy
* shard-resharding.png - share of keys that must move when the cluster grows
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from shared.sharding import VBUCKETS, assign_buckets, route, stable_hash

N_KEYS = 20_000
KEYS = range(1, N_KEYS + 1)


def place(keys, shards, strategy):
    table = assign_buckets(shards) if strategy == "vbucket" else None
    return {k: route(k, shards, strategy, table) for k in keys}


def shares(placed: dict[int, str], shards: list[str]) -> dict[str, float]:
    total = len(placed)
    return {s: sum(v == s for v in placed.values()) / total for s in shards}


def ring_owner(key: int, ring: list[tuple[int, str]]) -> str:
    """Classic consistent hashing: first virtual node clockwise of the key."""
    h = stable_hash(key)
    lo, hi = 0, len(ring)
    while lo < hi:
        mid = (lo + hi) // 2
        if ring[mid][0] < h:
            lo = mid + 1
        else:
            hi = mid
    return ring[lo % len(ring)][1]


def build_ring(shards: list[str], vnodes: int = 128) -> list[tuple[int, str]]:
    return sorted((stable_hash(f"{s}#{v}"), s) for s in shards for v in range(vnodes))


def moved_fraction(before: list[str], after: list[str], strategy: str) -> float:
    if strategy == "ring":
        rb, ra = build_ring(before), build_ring(after)
        return sum(ring_owner(k, rb) != ring_owner(k, ra) for k in KEYS) / N_KEYS
    pb, pa = place(KEYS, before, strategy), place(KEYS, after, strategy)
    return sum(pb[k] != pa[k] for k in KEYS) / N_KEYS


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/media")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    two = ["a", "b"]
    strategies = ["modulo", "hash", "vbucket"]

    # 1. balance --------------------------------------------------------------
    tenant_even = [k for k in KEYS if k % 2 == 0]  # one tenant, alternating ids
    print("## Placement of a tenant that owns every even id (10 000 clones)\n")
    print("| Strategy | Shard A % | Shard B % | Max/mean |")
    print("| --- | --- | --- | --- |")
    balance = {}
    for s in strategies:
        sh = shares(place(tenant_even, two, s), two)
        balance[s] = sh
        print(
            f"| {s} | {100 * sh['a']:.1f} | {100 * sh['b']:.1f} | "
            f"{max(sh.values()) / (1 / len(two)):.2f} |"
        )

    print("\n## Placement of sequential ids (no pattern)\n")
    print("| Strategy | Shard A % | Shard B % |")
    print("| --- | --- | --- |")
    for s in strategies:
        sh = shares(place(KEYS, two, s), two)
        print(f"| {s} | {100 * sh['a']:.1f} | {100 * sh['b']:.1f} |")

    # 2. resharding -----------------------------------------------------------
    steps = [
        (["a", "b"], ["a", "b", "c"]),
        (["a", "b"], ["a", "b", "c", "d"]),
        (["a", "b", "c"], ["a", "b", "c", "d"]),
    ]
    methods = ["modulo", "hash", "ring", "vbucket"]
    labels = {
        "modulo": "key % N",
        "hash": "hash(key) % N",
        "ring": "consistent hash ring (128 vnodes)",
        "vbucket": f"{VBUCKETS} virtual buckets (HRW)",
    }
    print("\n## Keys that must move when the cluster grows\n")
    print(
        "| Change | " + " | ".join(labels[m] for m in methods) + " | Ideal (1 - N/M) |"
    )
    print("| --- | " + " | ".join("---" for _ in methods) + " | --- |")
    results: dict[str, list[float]] = {m: [] for m in methods}
    names = []
    for before, after in steps:
        names.append(f"{len(before)} -> {len(after)}")
        row = []
        for m in methods:
            f = moved_fraction(before, after, m)
            results[m].append(100 * f)
            row.append(f"{100 * f:.1f}%")
        ideal = 1 - len(before) / len(after)
        print(f"| {names[-1]} shards | " + " | ".join(row) + f" | {100 * ideal:.1f}% |")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(matplotlib not installed: charts skipped)")
        return

    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    width = 0.35
    xs = range(len(strategies))
    ax.bar(
        [x - width / 2 for x in xs],
        [100 * balance[s]["a"] for s in strategies],
        width,
        label="Shard A",
        color="#c0392b",
    )
    ax.bar(
        [x + width / 2 for x in xs],
        [100 * balance[s]["b"] for s in strategies],
        width,
        label="Shard B",
        color="#2980b9",
    )
    ax.axhline(50, color="grey", linestyle="--", linewidth=0.8)
    ax.set_xticks(list(xs), strategies)
    ax.set_ylabel("share of the tenant's clones, %")
    ax.set_title("One tenant (all even ids) across 2 shards")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "shard-balance.png", dpi=130)

    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    width = 0.2
    colors = {
        "modulo": "#c0392b",
        "hash": "#e67e22",
        "ring": "#27ae60",
        "vbucket": "#2980b9",
    }
    for i, m in enumerate(methods):
        ax.bar(
            [x + (i - 1.5) * width for x in range(len(names))],
            results[m],
            width,
            label=labels[m],
            color=colors[m],
        )
    ax.plot(
        range(len(names)),
        [100 * (1 - len(b) / len(a)) for b, a in steps],
        "k--",
        marker="o",
        label="theoretical minimum",
    )
    ax.set_xticks(range(len(names)), [f"{n} shards" for n in names])
    ax.set_ylabel("keys that move, %")
    ax.set_title("Cost of adding shards")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "shard-resharding.png", dpi=130)
    print(f"\ncharts written to {out}")
    assert math.isfinite(sum(sum(v) for v in results.values()))


if __name__ == "__main__":
    main()
