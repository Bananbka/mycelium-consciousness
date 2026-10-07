"""Lab 7: before/after comparison of optimization stages.

    uv run --group load python loadtests/compare_stages.py \
        s0-before s1-code s2-cache

Reads `results/<stage>/{baseline,step}_*.csv` (filed there by
scripts/run_stage.sh), prints Markdown tables and writes comparison charts to
`docs/optimization/`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from profiles import PROFILES  # noqa: E402

RESULTS = Path("results")
OUT = Path("docs/optimization")
COLORS = ["#e76f51", "#e9c46a", "#2a9d8f", "#264653"]
LABELS = {
    "s0-before": "0 · before",
    "s1-code": "1 · query/IO/argon2 fixes",
    "s2-cache": "2 · + Redis cache",
}


def label(stage: str) -> str:
    return LABELS.get(stage, stage)


def history(stage: str, profile: str) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / stage / f"{profile}_stats_history.csv")
    df = df[df["Name"] == "Aggregated"].copy()
    df["t"] = df["Timestamp"] - df["Timestamp"].iloc[0]
    for column in ("95%", "50%", "Requests/s", "Failures/s", "User Count"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    # Locust's percentile columns in this CSV are cumulative over the whole run,
    # so they cannot describe "latency at this load step". The average can be
    # windowed from the cumulative counters: d(avg * count) / d(count).
    total = df["Total Average Response Time"] * df["Total Request Count"]
    count = df["Total Request Count"]
    df["avg_ms_w"] = (
        total.diff().rolling(30, min_periods=5).sum()
        / count.diff().rolling(30, min_periods=5).sum()
    )
    return df


def docker(stage: str, profile: str, hist: pd.DataFrame) -> pd.DataFrame:
    path = RESULTS / stage / f"{profile}_docker.csv"
    df = pd.read_csv(path)
    docker_start = path.stat().st_mtime - df["elapsed_s"].max()
    df["t"] = df["elapsed_s"] - (hist["Timestamp"].iloc[0] - docker_start)
    return df[df["t"] >= 0]


def total(stage: str, profile: str) -> pd.Series:
    stats = pd.read_csv(RESULTS / stage / f"{profile}_stats.csv")
    return stats[stats["Name"] == "Aggregated"].iloc[0]


def per_endpoint(stage: str, profile: str) -> pd.DataFrame:
    stats = pd.read_csv(RESULTS / stage / f"{profile}_stats.csv")
    return stats[stats["Name"] != "Aggregated"].set_index("Name")


def service(stage: str, profile: str, name: str) -> pd.Series:
    hist = history(stage, profile)
    df = docker(stage, profile, hist)
    df = df[df["container"] == name]
    return pd.Series(
        {"cpu_avg": df["cpu_percent"].mean(), "ram_peak": df["mem_mib"].max()}
    )


def step_table(stage: str) -> pd.DataFrame:
    hist = history(stage, "step")
    dk = docker(stage, "step", hist)
    rows = []
    start = 0
    for end, users, _ in PROFILES["step"].stages:
        lo = start + (end - start) / 3
        window = hist[(hist["t"] >= lo) & (hist["t"] < end)]
        if len(window):
            req = (
                window["Total Request Count"].iloc[-1]
                - window["Total Request Count"].iloc[0]
            )
            fail = (
                window["Total Failure Count"].iloc[-1]
                - window["Total Failure Count"].iloc[0]
            )
            api = dk[(dk["container"] == "api") & (dk["t"] >= lo) & (dk["t"] < end)]
            rows.append(
                {
                    "users": users,
                    "rps": window["Requests/s"].mean(),
                    "avg": (
                        (
                            window["Total Average Response Time"]
                            * window["Total Request Count"]
                        ).iloc[-1]
                        - (
                            window["Total Average Response Time"]
                            * window["Total Request Count"]
                        ).iloc[0]
                    )
                    / max(req, 1),
                    "err": 100 * fail / max(req, 1),
                    "api_cpu": api["cpu_percent"].mean(),
                }
            )
        start = end
    return pd.DataFrame(rows).set_index("users")


def pct_change(before: float, after: float) -> str:
    if not before:
        return "n/a"
    return f"{100 * (after - before) / before:+.0f}%"


def baseline_tables(stages: list[str]) -> str:
    lines = ["#### baseline (50 VUsers, 3 min)", ""]
    header = "| Metric | " + " | ".join(label(s) for s in stages) + " | Δ first→last |"
    lines += [header, "| --- |" + " --- |" * (len(stages) + 1)]
    totals = {s: total(s, "baseline") for s in stages}
    hists = {s: history(s, "baseline") for s in stages}
    api = {s: service(s, "baseline", "api") for s in stages}
    rows = {
        "RPS (avg)": lambda s: totals[s]["Requests/s"],
        "RPS (peak)": lambda s: hists[s]["Requests/s"].max(),
        "Latency avg, ms": lambda s: totals[s]["Average Response Time"],
        "Latency p50, ms": lambda s: totals[s]["50%"],
        "Latency p95, ms": lambda s: totals[s]["95%"],
        "Latency p99, ms": lambda s: totals[s]["99%"],
        "Error rate, %": lambda s: (
            100 * totals[s]["Failure Count"] / totals[s]["Request Count"]
        ),
        "api CPU avg, %": lambda s: api[s]["cpu_avg"],
        "api RAM peak, MiB": lambda s: api[s]["ram_peak"],
    }
    for name, fn in rows.items():
        vals = [fn(s) for s in stages]
        cells = " | ".join(f"{v:.2f}" if "rate" in name else f"{v:.0f}" for v in vals)
        lines.append(f"| {name} | {cells} | {pct_change(vals[0], vals[-1])} |")

    lines += ["", "Per endpoint, average / p95 ms:", ""]
    lines += [
        "| Endpoint | " + " | ".join(label(s) for s in stages) + " |",
        "| --- |" + " --- |" * len(stages),
    ]
    eps = {s: per_endpoint(s, "baseline") for s in stages}
    for name in eps[stages[0]].index:
        cells = " | ".join(
            f"{eps[s].loc[name, 'Average Response Time']:.0f} / {eps[s].loc[name, '95%']:.0f}"
            for s in stages
        )
        lines.append(f"| {name} | {cells} |")
    return "\n".join(lines) + "\n"


def step_tables(stages: list[str]) -> str:
    lines = ["#### step (50 → 600 VUsers, 9 min)", ""]
    totals = {s: total(s, "step") for s in stages}
    hists = {s: history(s, "step") for s in stages}
    api = {s: service(s, "step", "api") for s in stages}
    lines += [
        "| Metric | " + " | ".join(label(s) for s in stages) + " | Δ first→last |",
        "| --- |" + " --- |" * (len(stages) + 1),
    ]
    rows = {
        "Requests": lambda s: totals[s]["Request Count"],
        "RPS (avg)": lambda s: totals[s]["Requests/s"],
        "RPS (peak)": lambda s: hists[s]["Requests/s"].max(),
        "Latency avg, ms": lambda s: totals[s]["Average Response Time"],
        "Latency p50, ms": lambda s: totals[s]["50%"],
        "Latency p95, ms": lambda s: totals[s]["95%"],
        "Error rate, %": lambda s: (
            100 * totals[s]["Failure Count"] / totals[s]["Request Count"]
        ),
        "api CPU avg, %": lambda s: api[s]["cpu_avg"],
        "api RAM peak, MiB": lambda s: api[s]["ram_peak"],
    }
    for name, fn in rows.items():
        vals = [fn(s) for s in stages]
        cells = " | ".join(f"{v:.2f}" if "rate" in name else f"{v:.0f}" for v in vals)
        lines.append(f"| {name} | {cells} | {pct_change(vals[0], vals[-1])} |")

    tables = {s: step_table(s) for s in stages}
    lines += ["", "By load step (first third of each step discarded):", ""]
    head = "| Users |"
    sep = "| --- |"
    for s in stages:
        head += f" RPS {label(s)} | avg ms | err % |"
        sep += " --- | --- | --- |"
    lines += [head, sep]
    for users in tables[stages[0]].index:
        row = f"| {users} |"
        for s in stages:
            t = tables[s]
            if users in t.index:
                r = t.loc[users]
                row += f" {r['rps']:.0f} | {r['avg']:.0f} | {r['err']:.1f} |"
            else:
                row += " – | – | – |"
        lines.append(row)
    return "\n".join(lines) + "\n"


def charts(stages: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    for metric, ylabel, fname, log in (
        ("Requests/s", "requests / s", "step_throughput.png", False),
        (
            "avg_ms_w",
            "average response time, ms (30 s window)",
            "step_latency_avg.png",
            True,
        ),
    ):
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for color, s in zip(COLORS, stages, strict=False):
            h = history(s, "step")
            ax.plot(h["t"], h[metric], label=label(s), color=color)
        ax.set_xlabel("time, s (users 50 → 600, +50 every 45 s)")
        ax.set_ylabel(ylabel)
        if log:
            ax.set_yscale("log")
        ax.set_title(f"step: {ylabel}")
        ax.legend()
        fig.tight_layout()
        fig.savefig(OUT / fname, dpi=130)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    for color, s in zip(COLORS, stages, strict=False):
        t = step_table(s)
        ax.plot(t.index, t["err"], marker="o", label=label(s), color=color)
    ax.set_xlabel("virtual users")
    ax.set_ylabel("error rate, %")
    ax.set_title("step: error rate vs. users")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "step_errors.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    for color, s in zip(COLORS, stages, strict=False):
        h = history(s, "step")
        d = docker(s, "step", h)
        api = d[d["container"] == "api"]
        ax.plot(api["t"], api["cpu_percent"], label=label(s), color=color)
    ax.set_xlabel("time, s")
    ax.set_ylabel("api CPU, % of one core")
    ax.set_title("step: api CPU")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "step_api_cpu.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    names = [label(s) for s in stages]
    base = {s: total(s, "baseline") for s in stages}
    for ax, (column, title) in zip(
        axes,
        (
            ("Requests/s", "RPS"),
            ("Average Response Time", "avg latency, ms"),
            ("95%", "p95 latency, ms"),
        ),
        strict=True,
    ):
        ax.bar(range(len(stages)), [base[s][column] for s in stages], color=COLORS)
        ax.set_xticks(range(len(stages)), [str(i) for i in range(len(stages))])
        ax.set_title(f"baseline: {title}")
    fig.text(0.5, 0.0, "   ".join(names), ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT / "baseline_bars.png", dpi=130)
    plt.close(fig)


def main() -> None:
    os.chdir(Path(__file__).parent.parent)
    stages = sys.argv[1:] or ["s0-before", "s1-code", "s2-cache"]
    charts(stages)
    text = baseline_tables(stages) + "\n" + step_tables(stages)
    (OUT / "summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
