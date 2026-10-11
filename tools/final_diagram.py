# ruff: noqa: E501
"""Lab 10: C4 container diagram of the final architecture -> docs/media/c4-final.png.

    uv run --with matplotlib python tools/final_diagram.py

Only containers that exist in infra/docker-compose.yaml are drawn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent.parent / "docs" / "media" / "c4-final.png"

COLORS = {
    "person": "#08427b",
    "edge": "#1168bd",
    "app": "#438dd5",
    "cache": "#c0392b",
    "db": "#2e7d32",
    "store": "#6d4c41",
    "worker": "#8e44ad",
}

fig, ax = plt.subplots(figsize=(15, 9.5))
ax.set_xlim(0, 15)
ax.set_ylim(0, 9.5)
ax.axis("off")

BOXES: dict[str, tuple[float, float, float, float]] = {}


def box(key, x, y, w, h, title, sub, kind):
    BOXES[key] = (x, y, w, h)
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.02,rounding_size=0.12",
            fc=COLORS[kind],
            ec="#1c1c1c",
            lw=1.2,
        )
    )
    ax.text(
        x + w / 2,
        y + h - 0.32,
        title,
        ha="center",
        va="center",
        color="white",
        fontsize=10.5,
        fontweight="bold",
    )
    ax.text(
        x + w / 2,
        y + h / 2 - 0.18,
        sub,
        ha="center",
        va="center",
        color="white",
        fontsize=8.2,
        linespacing=1.35,
    )


def edge(a, b, label="", dashed=False, rad=0.0, la=0.5):
    ax_, ay, aw, ah = BOXES[a]
    bx, by, bw, bh = BOXES[b]
    ca = (ax_ + aw / 2, ay + ah / 2)
    cb = (bx + bw / 2, by + bh / 2)

    # leave from the box side facing the target
    def anchor(x, y, w, h, tx, ty):
        dx, dy = tx - (x + w / 2), ty - (y + h / 2)
        if abs(dx) / w > abs(dy) / h:
            return (x + (w if dx > 0 else 0), y + h / 2)
        return (x + w / 2, y + (h if dy > 0 else 0))

    p1 = anchor(ax_, ay, aw, ah, *cb)
    p2 = anchor(bx, by, bw, bh, *ca)
    ax.add_patch(
        FancyArrowPatch(
            p1,
            p2,
            arrowstyle="-|>",
            mutation_scale=13,
            lw=1.3,
            ls="--" if dashed else "-",
            color="#333",
            connectionstyle=f"arc3,rad={rad}",
        )
    )
    if label:
        mx = p1[0] + (p2[0] - p1[0]) * la
        my = p1[1] + (p2[1] - p1[1]) * la
        ax.text(
            mx,
            my + 0.12,
            label,
            ha="center",
            fontsize=7.6,
            bbox={"fc": "white", "ec": "none", "alpha": 0.85, "pad": 0.8},
        )


box("client", 0.3, 4.0, 1.9, 1.4, "Clone / Operator", "JWT bearer\nHTTP/JSON", "person")
box("nginx", 3.0, 4.0, 1.9, 1.4, "nginx", "least_conn\nSPOF (single LB)", "edge")
box(
    "api",
    5.8,
    3.7,
    2.6,
    2.0,
    "api x N (FastAPI)",
    "stateless, 2 uvicorn workers\nShard Router (in-process)\nguarded cache, Bloom filter\nreplica-aware reads, MinIO restore",
    "app",
)
box(
    "redis",
    5.5,
    0.4,
    2.6,
    1.7,
    "Redis",
    "memory streams, cache,\nlocks, Bloom bitmap,\nread-after-write markers,\nCelery broker",
    "cache",
)
box(
    "primary",
    10.6,
    8.0,
    2.4,
    1.3,
    "Postgres primary",
    "control plane\nusers, clone_profiles",
    "db",
)
box(
    "replica",
    10.6,
    6.2,
    2.4,
    1.3,
    "Postgres replica",
    "streaming, hot standby\nlag-tolerant reads",
    "db",
)
box(
    "shard_a",
    10.6,
    4.4,
    2.4,
    1.3,
    "shard-a",
    "data plane\nmemory_buffers/backups",
    "db",
)
box(
    "shard_b",
    10.6,
    2.6,
    2.4,
    1.3,
    "shard-b",
    "data plane\nmemory_buffers/backups",
    "db",
)
box(
    "worker",
    8.4,
    0.4,
    2.4,
    1.7,
    "celery-worker / beat",
    "flush streams, rollup,\nroutes by clone_id",
    "worker",
)
box(
    "minio",
    11.9,
    0.4,
    2.4,
    1.7,
    "MinIO (S3)",
    "backup archives\n(storage_key only\nin Postgres)",
    "store",
)

edge("client", "nginx", "HTTPS")
edge("nginx", "api", "least_conn")
edge("api", "redis", "XADD, cache,\nlock, Bloom", la=0.5)
edge("api", "primary", "auth, writes\n(strong)", la=0.72)
edge("api", "replica", "reads (eventual)", dashed=True, la=0.72)
edge("api", "shard_a", "hash(clone_id)", la=0.72)
edge("api", "shard_b", "", la=0.72)
edge("worker", "redis", "XREAD, trim", la=0.5)
edge("worker", "shard_b", "flush, rollup\n(both shards)", la=0.5)
edge("worker", "minio", "put_object", la=0.5)
edge("primary", "replica", "WAL", la=0.5)

ax.text(
    0.3,
    9.1,
    "Mycelium Consciousness - final architecture (C4 container level)",
    fontsize=14,
    fontweight="bold",
)
ax.text(
    0.3,
    8.65,
    "solid: request path / data flow   dashed: tolerated-stale or fallback-capable path",
    fontsize=9,
    color="#444",
)
fig.savefig(OUT, dpi=130, bbox_inches="tight")
print(f"wrote {OUT}", file=sys.stderr)
