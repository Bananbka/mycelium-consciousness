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
OUT = Path("docs/load-testing")


def _history(name: str) -> pd.DataFrame:
    df = pd.read_csv(RESULTS / f"{name}_stats_history.csv")
    df = df[df["Name"] == "Aggregated"].copy()
    df["t"] = df["Timestamp"] - df["Timestamp"].iloc[0]
    for column in ("95%", "50%", "Requests/s", "Failures/s", "User Count"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _docker(name: str, history: pd.DataFrame) -> pd.DataFrame:
    path = RESULTS / f"{name}_docker.csv"
    df = pd.read_csv(path)
    # The collector starts before Locust provisions accounts; line the two clocks
    # up using the file's last write time and the last sample's elapsed time.
    docker_start = path.stat().st_mtime - df["elapsed_s"].max()
    locust_start = history["Timestamp"].iloc[0]
    df["t"] = df["elapsed_s"] - (locust_start - docker_start)
    return df[df["t"] >= 0]


def charts(name: str) -> None:
    hist = _history(name)
    docker = _docker(name, hist)
    OUT.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(hist["t"], hist["50%"], label="p50", color="#2a9d8f")
    ax.plot(hist["t"], hist["95%"], label="p95", color="#e76f51")
    ax.set_xlabel("time, s")
    ax.set_ylabel("response time, ms")
    ax.set_yscale("log")
    ax2 = ax.twinx()
    ax2.plot(
        hist["t"], hist["User Count"], color="#999999", linestyle="--", label="users"
    )
    ax2.set_ylabel("virtual users")
    lines = ax.get_legend_handles_labels()
    lines2 = ax2.get_legend_handles_labels()
    ax.legend(lines[0] + lines2[0], lines[1] + lines2[1], loc="upper left")
    ax.set_title(f"{name}: response time vs. users")
    fig.tight_layout()
    fig.savefig(OUT / f"{name}_latency.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(hist["t"], hist["Requests/s"], label="requests/s", color="#264653")
    ax.plot(hist["t"], hist["Failures/s"], label="failures/s", color="#d62828")
    ax.set_xlabel("time, s")
    ax.set_ylabel("per second")
    ax2 = ax.twinx()
    ax2.plot(
        hist["t"], hist["User Count"], color="#999999", linestyle="--", label="users"
    )
    ax2.set_ylabel("virtual users")
    lines = ax.get_legend_handles_labels()
    lines2 = ax2.get_legend_handles_labels()
    ax.legend(lines[0] + lines2[0], lines[1] + lines2[1], loc="upper left")
    ax.set_title(f"{name}: throughput vs. users")
    fig.tight_layout()
    fig.savefig(OUT / f"{name}_throughput.png", dpi=130)
    plt.close(fig)

    fig, (cpu, mem) = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for service, group in docker.groupby("container"):
        if service in {"minio", "celery-beat"}:
            continue
        cpu.plot(group["t"], group["cpu_percent"], label=service)
        mem.plot(group["t"], group["mem_mib"], label=service)
    cpu.set_ylabel("CPU, % of one core")
    mem.set_ylabel("memory, MiB")
    mem.set_xlabel("time, s")
    cpu.legend(ncol=3, fontsize=8)
    cpu.set_title(f"{name}: server resources")
    fig.tight_layout()
    fig.savefig(OUT / f"{name}_resources.png", dpi=130)
    plt.close(fig)


def summary(name: str) -> str:
    stats = pd.read_csv(RESULTS / f"{name}_stats.csv")
    hist = _history(name)
    docker = _docker(name, hist)
    total = stats[stats["Name"] == "Aggregated"].iloc[0]

    lines = [
        f"### {name}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Requests | {int(total['Request Count'])} |",
        f"| RPS (average / peak) | {total['Requests/s']:.1f} / {hist['Requests/s'].max():.1f} |",
        f"| Latency avg / min / max | {total['Average Response Time']:.0f} / "
        f"{total['Min Response Time']:.0f} / {total['Max Response Time']:.0f} ms |",
        f"| Latency p50 / p95 / p99 | {total['50%']:.0f} / {total['95%']:.0f} / "
        f"{total['99%']:.0f} ms |",
        f"| Error rate | {100 * total['Failure Count'] / total['Request Count']:.2f}% "
        f"({int(total['Failure Count'])}) |",
        "",
        "| Endpoint | Requests | Avg ms | p95 ms | p99 ms | Max ms | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for _, row in stats[stats["Name"] != "Aggregated"].iterrows():
        lines.append(
            f"| {row['Name']} | {int(row['Request Count'])} | "
            f"{row['Average Response Time']:.0f} | {row['95%']:.0f} | {row['99%']:.0f} | "
            f"{row['Max Response Time']:.0f} | {int(row['Failure Count'])} |"
        )

    lines += [
        "",
        "| Service | CPU avg % | CPU peak % | RAM peak MiB |",
        "| --- | --- | --- | --- |",
    ]
    for service, group in docker.groupby("container"):
        lines.append(
            f"| {service} | {group['cpu_percent'].mean():.0f} | "
            f"{group['cpu_percent'].max():.0f} | {group['mem_mib'].max():.0f} |"
        )

    if name in PROFILES and len(PROFILES[name].stages) > 3:
        lines += [
            "",
            "| Users | RPS | p50 ms | p95 ms | Error % | API CPU % |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        start = 0
        for end, users, _ in PROFILES[name].stages:
            # Skip the first third of each step: users are still spawning.
            window = hist[(hist["t"] >= start + (end - start) / 3) & (hist["t"] < end)]
            if len(window):
                requests_delta = (
                    window["Total Request Count"].iloc[-1]
                    - window["Total Request Count"].iloc[0]
                )
                failures_delta = (
                    window["Total Failure Count"].iloc[-1]
                    - window["Total Failure Count"].iloc[0]
                )
                api = docker[
                    (docker["container"] == "api")
                    & (docker["t"] >= start + (end - start) / 3)
                    & (docker["t"] < end)
                ]["cpu_percent"].mean()
                lines.append(
                    f"| {users} | {window['Requests/s'].mean():.0f} | "
                    f"{window['50%'].mean():.0f} | {window['95%'].mean():.0f} | "
                    f"{100 * failures_delta / max(requests_delta, 1):.2f} | {api:.0f} |"
                )
            start = end

    return "\n".join(lines) + "\n"


def main() -> None:
    os.chdir(Path(__file__).parent.parent)
    for name in sys.argv[1:] or ["baseline", "step"]:
        charts(name)
        text = summary(name)
        (OUT / f"{name}_summary.md").write_text(text, encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
