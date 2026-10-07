"""Lab 8: soak / stress / spike analysis and charts.

    uv run --group load python loadtests/final_report.py soak   results/lab8-soak
    uv run --group load python loadtests/final_report.py stress results/lab8-stress
    uv run --group load python loadtests/final_report.py spike  results/lab8-stress

Reads the Locust CSVs and the docker-stats CSV that scripts/run_stage.sh filed
under the given directory, prints Markdown tables and writes charts to
`docs/stability/`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from profiles import PROFILES  # noqa: E402

OUT = Path("docs/stability")
# Success criteria from the lab: CPU <= 80 %, RAM <= 80 %, errors <= 0.1 %.
CPU_LIMIT, RAM_LIMIT, ERR_LIMIT = 80.0, 80.0, 0.1


def host_resources() -> tuple[int, float]:
    out = subprocess.run(
        ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return int(out[0]), float(out[1]) / 1024**2  # cores, MiB


def history(folder: Path, profile: str) -> pd.DataFrame:
    df = pd.read_csv(folder / f"{profile}_stats_history.csv")
    df = df[df["Name"] == "Aggregated"].copy()
    df["t"] = df["Timestamp"] - df["Timestamp"].iloc[0]
    for column in ("95%", "50%", "99%", "Requests/s", "Failures/s", "User Count"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def docker(folder: Path, profile: str, hist: pd.DataFrame) -> pd.DataFrame:
    """Per-service CPU/RAM, replicas summed, on the Locust clock."""
    path = folder / f"{profile}_docker.csv"
    df = pd.read_csv(path)
    docker_start = path.stat().st_mtime - df["elapsed_s"].max()
    shift = hist["Timestamp"].iloc[0] - docker_start
    df["t"] = df["elapsed_s"] - shift
    df = df[df["t"] >= 0]
    return df.groupby(["t", "container"], as_index=False)[
        ["cpu_percent", "mem_mib"]
    ].sum()


def prom(folder: Path, profile: str) -> pd.DataFrame:
    """Server-side, windowed series dumped by loadtests/prom_export.py."""
    return pd.read_csv(folder / f"{profile}_prom.csv")


def windowed_avg_ms(hist: pd.DataFrame) -> pd.Series:
    """Client-side average latency per sampling interval from cumulative counters.

    Locust's percentile columns in the history CSV are cumulative over the run
    (they decay slowly after a spike), so only the average can be windowed.
    """
    total = hist["Total Average Response Time"] * hist["Total Request Count"]
    delta = total.diff().rolling(30, min_periods=5).sum()
    count = hist["Total Request Count"].diff().rolling(30, min_periods=5).sum()
    return delta / count


def totals(folder: Path, profile: str) -> pd.Series:
    stats = pd.read_csv(folder / f"{profile}_stats.csv")
    return stats[stats["Name"] == "Aggregated"].iloc[0]


def host_series(dk: pd.DataFrame, cores: int, mem_mib: float) -> pd.DataFrame:
    # Round to the sampling batch so services line up on one timestamp.
    dk = dk.assign(tb=dk["t"].round(0))
    agg = dk.groupby("tb")[["cpu_percent", "mem_mib"]].sum()
    return pd.DataFrame(
        {
            "cpu_pct": agg["cpu_percent"] / cores,
            "ram_pct": 100 * agg["mem_mib"] / mem_mib,
        }
    )


def slope_per_hour(t: pd.Series, y: pd.Series) -> float:
    if len(t) < 3:
        return float("nan")
    return float(np.polyfit(t / 3600, y, 1)[0])


def _line(ax, df, col, label, scale=1.0, **kw):
    ax.plot(df["t"] / 60, df[col] * scale, label=label, **kw)


def soak(folder: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cores, mem_mib = host_resources()
    hist = history(folder, "soak")
    dk = docker(folder, "soak", hist)
    pr = prom(folder, "soak")
    tot = totals(folder, "soak")
    ramp = PROFILES["soak"].stages[0][0]
    plateau_start = ramp + 60
    steady = hist[hist["t"] >= plateau_start]
    ps = pr[pr["t"] >= plateau_start].copy()
    host = host_series(dk[dk["t"] >= plateau_start], cores, mem_mib)
    err = 100 * tot["Failure Count"] / tot["Request Count"]
    minutes = hist["t"].max() / 60

    def edge(df: pd.DataFrame, which: str) -> pd.DataFrame:
        return (
            df[df["t"] < df["t"].min() + 600]
            if which == "first"
            else df[df["t"] > df["t"].max() - 600]
        )

    print(f"Soak: {minutes:.0f} min, host {cores} cores / {mem_mib / 1024:.1f} GiB\n")
    print("| Metric | Value |\n| --- | --- |")
    print(f"| Requests | {int(tot['Request Count'])} |")
    print(
        f"| RPS (run average / plateau mean / peak) | {tot['Requests/s']:.1f} / "
        f"{steady['Requests/s'].mean():.1f} / {steady['Requests/s'].max():.1f} |"
    )
    print(
        f"| Latency (client) avg / min / max | {tot['Average Response Time']:.0f} / "
        f"{tot['Min Response Time']:.0f} / {tot['Max Response Time']:.0f} ms |"
    )
    print(
        f"| Latency (client) p50 / p95 / p99 | {tot['50%']:.0f} / {tot['95%']:.0f} / "
        f"{tot['99%']:.0f} ms |"
    )
    print(
        f"| Latency (server, plateau mean) p50 / p95 / p99 | "
        f"{1000 * ps['p50'].mean():.1f} / {1000 * ps['p95'].mean():.1f} / "
        f"{1000 * ps['p99'].mean():.1f} ms |"
    )
    print(
        f"| Error rate | {err:.3f} % ({int(tot['Failure Count'])} of "
        f"{int(tot['Request Count'])}) |"
    )
    cpu_max = host["cpu_pct"].rolling(12).mean().max()
    print(
        f"| Host CPU (mean / max 1-min of {cores} cores) | "
        f"{host['cpu_pct'].mean():.1f} / {cpu_max:.1f} % |"
    )
    print(
        f"| Host RAM (mean / max of {mem_mib / 1024:.1f} GiB) | "
        f"{host['ram_pct'].mean():.1f} / {host['ram_pct'].max():.1f} % |"
    )

    print(
        "\nCriteria:\n\n| Criterion | Target | Measured | Result |\n| --- | --- | --- | --- |"
    )
    for name, target, value in (
        ("CPU (max, 1-min mean)", CPU_LIMIT, cpu_max),
        ("RAM (max)", RAM_LIMIT, host["ram_pct"].max()),
        ("Error rate", ERR_LIMIT, err),
    ):
        print(
            f"| {name} | <= {target:g} % | {value:.2f} % | "
            f"{'PASS' if value <= target else 'FAIL'} |"
        )

    print("\nDegradation check (plateau, first vs last 10 min; trend = linear fit):\n")
    print(
        "| Metric | First 10 min | Last 10 min | Trend / hour |\n| --- | --- | --- | --- |"
    )
    avg_ms = windowed_avg_ms(hist).where(hist["t"] >= plateau_start)
    rows = (
        ("Server p50, ms", ps["t"], 1000 * ps["p50"]),
        ("Server p95, ms", ps["t"], 1000 * ps["p95"]),
        ("Server p99, ms", ps["t"], 1000 * ps["p99"]),
        ("Client avg latency, ms", hist["t"], avg_ms),
        ("RPS (client)", steady["t"], steady["Requests/s"]),
    )
    for label, t, y in rows:
        d = pd.DataFrame({"t": t, "y": y}).dropna()
        d = d[d["t"] >= plateau_start]
        print(
            f"| {label} | {edge(d, 'first')['y'].mean():.1f} | "
            f"{edge(d, 'last')['y'].mean():.1f} | {slope_per_hour(d['t'], d['y']):+.2f} |"
        )

    print("\nMemory / state by service (plateau only):\n")
    print(
        "| Series | First 10 min | Last 10 min | Max | Trend / hour |\n"
        "| --- | --- | --- | --- | --- |"
    )
    for name, g in dk[dk["t"] >= plateau_start].groupby("container"):
        d = g.rename(columns={"mem_mib": "y"})
        print(
            f"| RAM {name}, MiB | {edge(d, 'first')['y'].mean():.0f} | "
            f"{edge(d, 'last')['y'].mean():.0f} | {d['y'].max():.0f} | "
            f"{slope_per_hour(d['t'], d['y']):+.1f} |"
        )
    for col, label in (
        ("pg_backends", "Postgres connections"),
        ("redis_mem_bytes", "Redis memory, MiB"),
        ("redis_clients", "Redis clients"),
        ("nginx_active", "nginx active connections"),
        ("db_size_mib", "Database size, MiB"),
    ):
        d = ps[["t", col]].dropna().rename(columns={col: "y"})
        if col == "redis_mem_bytes":
            d["y"] = d["y"] / 1048576
        if d.empty:
            continue
        print(
            f"| {label} | {edge(d, 'first')['y'].mean():.1f} | "
            f"{edge(d, 'last')['y'].mean():.1f} | {d['y'].max():.1f} | "
            f"{slope_per_hour(d['t'], d['y']):+.2f} |"
        )

    print("\nCPU by service (plateau mean, replicas summed, % of one core):\n")
    for name, g in dk[dk["t"] >= plateau_start].groupby("container"):
        first = edge(g, "first")["cpu_percent"].mean()
        last = edge(g, "last")["cpu_percent"].mean()
        print(
            f"- {name}: {g['cpu_percent'].mean():.0f} "
            f"(first 10 min {first:.0f}, last 10 min {last:.0f})"
        )

    # Charts
    fig, ax = plt.subplots(figsize=(11, 4.5))
    for col, color in (("p50", "#2a9d8f"), ("p95", "#e76f51"), ("p99", "#264653")):
        _line(ax, pr, col, col, 1000, color=color)
    ax.set_xlabel("time, min")
    ax.set_ylabel("server-side latency, ms")
    ax.set_yscale("log")
    ax2 = ax.twinx()
    _line(ax2, hist, "Requests/s", "RPS", color="#999999", lw=0.6)
    ax2.set_ylabel("requests / s")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=4)
    ax.set_title("soak: latency percentiles (1-min window) and throughput")
    fig.tight_layout()
    fig.savefig(OUT / "soak_latency.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.5))
    _line(ax, pr, "cpu_host_pct", "CPU, % of host", color="#264653")
    _line(ax, pr, "ram_host_pct", "RAM, % of host", color="#e76f51")
    ax.axhline(80, color="#d62828", ls="--", lw=1, label="80 % limit")
    ax.set_ylim(0, 100)
    ax.set_xlabel("time, min")
    ax.set_ylabel("%")
    ax.legend()
    ax.set_title("soak: host utilization (all containers)")
    fig.tight_layout()
    fig.savefig(OUT / "soak_host.png", dpi=130)
    plt.close(fig)

    fig, (cpu, mem) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for name, g in dk.groupby("container"):
        if g["mem_mib"].max() < 30:
            continue
        g = g.sort_values("t")
        cpu.plot(
            g["t"] / 60, g["cpu_percent"].rolling(12, min_periods=1).mean(), label=name
        )
        mem.plot(g["t"] / 60, g["mem_mib"], label=name)
    cpu.set_ylabel("CPU, % of one core (1-min mean)")
    mem.set_ylabel("RAM, MiB")
    mem.set_xlabel("time, min")
    mem.legend(ncol=4, fontsize=8)
    cpu.set_title("soak: per-service resources")
    fig.tight_layout()
    fig.savefig(OUT / "soak_services.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5), sharex=True)
    for ax, col, title, scale in (
        (axes[0, 0], "pg_backends", "Postgres connections", 1),
        (axes[0, 1], "db_size_mib", "Database size, MiB", 1),
        (axes[1, 0], "redis_mem_bytes", "Redis memory, MiB", 1 / 1048576),
        (axes[1, 1], "nginx_active", "nginx active connections", 1),
    ):
        _line(ax, pr.dropna(subset=[col]), col, title, scale, color="#264653")
        ax.set_title(title, fontsize=10)
    for ax in axes[1]:
        ax.set_xlabel("time, min")
    fig.tight_layout()
    fig.savefig(OUT / "soak_state.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 3.5))
    _line(ax, hist, "Failures/s", "failures/s", color="#d62828")
    ax.set_xlabel("time, min")
    ax.set_ylabel("failures / s")
    ax.set_title("soak: failures per second (client side)")
    fig.tight_layout()
    fig.savefig(OUT / "soak_failures.png", dpi=130)
    plt.close(fig)


def _windows(profile: str):
    start = 0
    for end, users, _ in PROFILES[profile].stages:
        yield start, end, users
        start = end


def stress(folder: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hist = history(folder, "stress")
    pr = prom(folder, "stress")
    rows = []
    for start, end, users in _windows("stress"):
        lo = start + (end - start) / 3
        w = hist[(hist["t"] >= lo) & (hist["t"] < end)]
        p = pr[(pr["t"] >= lo) & (pr["t"] < end)]
        req = w["Total Request Count"].iloc[-1] - w["Total Request Count"].iloc[0]
        fail = w["Total Failure Count"].iloc[-1] - w["Total Failure Count"].iloc[0]
        rows.append(
            {
                "users": users,
                "rps": w["Requests/s"].mean(),
                "p50": 1000 * p["p50_api"].mean(),
                "p95": 1000 * p["p95_api"].mean(),
                "p99": 1000 * p["p99_api"].mean(),
                "login": 1000 * p["p95_login"].mean(),
                "err": 100 * fail / max(req, 1),
                "api_cpu": p["api_cpu"].mean(),
                "host_cpu": p["cpu_host_pct"].mean(),
                "pg": p["pg_backends"].mean(),
            }
        )
    table = pd.DataFrame(rows)
    print(
        "| Users | RPS | p50 ms | p95 ms | p99 ms | login p95 ms | Error % "
        "| CPU api % | CPU host % | PG conns |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for _, r in table.iterrows():
        print(
            f"| {int(r['users'])} | {r['rps']:.0f} | {r['p50']:.0f} | {r['p95']:.0f} | "
            f"{r['p99']:.0f} | {r['login']:.0f} | {r['err']:.2f} | {r['api_cpu']:.0f} | "
            f"{r['host_cpu']:.0f} | {r['pg']:.0f} |"
        )
    best = table.loc[table["rps"].idxmax()]
    knee = table[table["p95"] > 100]
    broken = table[(table["err"] > 1.0) | (table["p95"] > 1000)]
    print("\n(percentiles are server-side, 1-min window, excluding /auth/login)")
    print(f"\nPeak sustained RPS: {best['rps']:.0f} at {int(best['users'])} users.")
    if len(knee):
        print(
            f"First step with server p95 > 100 ms: {int(knee.iloc[0]['users'])} users."
        )
    if len(broken):
        print(
            "Break point (err > 1 % or server p95 > 1 s): "
            f"{int(broken.iloc[0]['users'])} users."
        )
    else:
        print("No break point (err > 1 % or server p95 > 1 s) within the profile.")

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    a1.plot(hist["t"], hist["Requests/s"], color="#264653", label="RPS")
    a1.plot(hist["t"], hist["Failures/s"], color="#d62828", label="failures/s")
    a1.set_ylabel("per second")
    a1b = a1.twinx()
    a1b.plot(hist["t"], hist["User Count"], color="#999999", ls="--")
    a1b.set_ylabel("users")
    a1.legend(loc="upper left")
    for col, color in (("p50", "#2a9d8f"), ("p95", "#e76f51"), ("p99", "#264653")):
        a2.plot(pr["t"], 1000 * pr[f"{col}_api"], color=color, label=f"server {col}")
    a2.plot(pr["t"], 1000 * pr["p95_login"], color="#999999", ls=":", label="login p95")
    a2.set_yscale("log")
    a2.set_ylabel("ms (excl. login unless noted)")
    a2.set_xlabel("time, s")
    a2.legend()
    a1.set_title("stress: +100 users / 30 s")
    fig.tight_layout()
    fig.savefig(OUT / "stress.png", dpi=130)
    plt.close(fig)


def spike(folder: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hist = history(folder, "spike")
    pr = prom(folder, "spike")
    stages = PROFILES["spike"].stages
    spike_start, spike_end = stages[0][0], stages[1][0]
    pre = pr[(pr["t"] > 60) & (pr["t"] < spike_start)]
    surge = pr[(pr["t"] >= spike_start) & (pr["t"] < spike_start + 60)]
    plateau = pr[(pr["t"] > spike_start + 90) & (pr["t"] < spike_end)]
    base_p95 = pre["p95_api"].mean()

    after = pr[pr["t"] >= spike_end].reset_index(drop=True)
    ok = (after["p95_api"] <= max(3 * base_p95, 0.02)) & (after["err_pct"] == 0)
    streak = ok.rolling(3).sum() == 3
    recovery = (
        float(after["t"][streak].iloc[0] - spike_end) if streak.any() else float("nan")
    )
    total = totals(folder, "spike")
    failures = hist[hist["Failures/s"] > 0]

    print("| Phase | RPS (server) | p50 ms | p95 ms | p99 ms | login p95 ms |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, w in (
        ("before spike (100 users)", pre),
        ("first 60 s of the jump to 800", surge),
        ("spike plateau (800 users)", plateau),
        ("last 3 min (back to 100)", pr[pr["t"] > pr["t"].max() - 180]),
    ):
        print(
            f"| {name} | {w['rps'].mean():.0f} | {1000 * w['p50_api'].mean():.0f} | "
            f"{1000 * w['p95_api'].mean():.0f} | {1000 * w['p99_api'].mean():.0f} | "
            f"{1000 * w['p95_login'].mean():.0f} |"
        )
    print(
        f"\nClient side: {int(total['Request Count'])} requests, "
        f"{100 * total['Failure Count'] / total['Request Count']:.2f} % errors "
        f"({int(total['Failure Count'])}); failures occurred between "
        f"t={failures['t'].min():.0f} s and t={failures['t'].max():.0f} s "
        f"(spike began at t={spike_start} s)."
    )
    print(
        f"Server p95 before the spike {1000 * base_p95:.1f} ms; back within 3x of it "
        f"(and error-free) {recovery:.0f} s after the load dropped."
    )

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    a1.plot(hist["t"], hist["Requests/s"], color="#264653", label="RPS (client)")
    a1.plot(hist["t"], hist["Failures/s"], color="#d62828", label="failures/s")
    a1b = a1.twinx()
    a1b.plot(hist["t"], hist["User Count"], color="#999999", ls="--")
    a1b.set_ylabel("users")
    a1.legend(loc="upper left")
    for col, color in (("p50", "#2a9d8f"), ("p95", "#e76f51"), ("p99", "#264653")):
        a2.plot(pr["t"], 1000 * pr[f"{col}_api"], color=color, label=f"server {col}")
    a2.plot(pr["t"], 1000 * pr["p95_login"], color="#999999", ls=":", label="login p95")
    a2.axvspan(spike_start, spike_end, color="#f4a261", alpha=0.15)
    a2.set_yscale("log")
    a2.set_ylabel("ms")
    a2.set_xlabel("time, s")
    a2.legend()
    a1.set_title("spike: 100 → 800 → 100 users")
    fig.tight_layout()
    fig.savefig(OUT / "spike.png", dpi=130)
    plt.close(fig)


def main() -> None:
    os.chdir(Path(__file__).parent.parent)
    mode, folder = sys.argv[1], Path(sys.argv[2])
    {"soak": soak, "stress": stress, "spike": spike}[mode](folder)


if __name__ == "__main__":
    main()
