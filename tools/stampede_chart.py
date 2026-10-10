"""Chart the avalanche experiment: database computations per second over time.

uv run --with matplotlib python tools/stampede_chart.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SRC = Path("load-tests/results/l8_avalanche.jsonl")
OUT = Path("docs/media/cache-avalanche.png")


def main() -> None:
    runs = [json.loads(line) for line in SRC.read_text().splitlines() if line]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, mode in zip(axes, ("none", "lock"), strict=True):
        for run in (r for r in runs if r["mode"] == mode):
            series = {int(k): v for k, v in run["computes_per_second"].items()}
            xs = sorted(series)
            jitter = run["label"].removeprefix("jitter")
            ax.plot(
                xs,
                [series[x] for x in xs],
                marker="o",
                markersize=3,
                label=f"jitter {jitter}",
            )
        ax.axvline(runs[0]["ttl_s"], color="grey", linestyle=":", linewidth=1)
        ax.axvline(2 * runs[0]["ttl_s"], color="grey", linestyle=":", linewidth=1)
        ax.set_title(f"protection = {mode}")
        ax.set_xlabel("seconds since all keys were warmed")
        ax.legend()
    axes[0].set_ylabel("database computations / s")
    fig.suptitle("60 keys, TTL 10 s: synchronised expiry vs jittered expiry")
    fig.tight_layout()
    fig.savefig(OUT, dpi=130)
    print(OUT)


if __name__ == "__main__":
    main()
