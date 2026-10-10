"""Chart the live hot-shard experiment (before/after the shard strategy change).

    uv run --with matplotlib python tools/shard_chart.py RESULTS.jsonl

Input: one JSON object per line as printed by `tools/shard_load.py hot`
(several passes are averaged per strategy). Output: docs/media/shard-hotshard.png.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ORDER = ["modulo", "hash", "vbucket"]


def main() -> None:
    src = Path(sys.argv[1])
    runs: dict[str, list[dict]] = defaultdict(list)
    for line in src.read_text().splitlines():
        if line.strip().startswith("{"):
            row = json.loads(line)
            runs[row["label"]].append(row)

    labels = [s for s in ORDER if s in runs]

    def mean(label: str, key: str, shard: str) -> float:
        return statistics.fmean(r[key].get(shard, 0.0) for r in runs[label])

    fig, (left, right) = plt.subplots(1, 2, figsize=(9, 3.8))
    x = range(len(labels))
    width = 0.36
    for ax, key, title in (
        (left, "rows_read_share_pct", "Share of rows read, %"),
        (right, "db_cpu_pct", "Database CPU, % of one core"),
    ):
        ax.bar(
            [i - width / 2 for i in x],
            [mean(s, key, "a") for s in labels],
            width,
            label="Shard A",
            color="#c0392b",
        )
        ax.bar(
            [i + width / 2 for i in x],
            [mean(s, key, "b") for s in labels],
            width,
            label="Shard B",
            color="#2980b9",
        )
        ax.set_xticks(list(x), labels)
        ax.set_title(title)
    right.legend()
    fig.suptitle("One tenant (even clone ids) reading its backups: before -> after")
    fig.tight_layout()
    out = Path("docs/media/shard-hotshard.png")
    fig.savefig(out, dpi=130)
    print(out)


if __name__ == "__main__":
    main()
