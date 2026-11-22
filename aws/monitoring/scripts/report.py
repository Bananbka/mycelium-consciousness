# ruff: noqa: E501
"""Read the metrics back from CloudWatch, compute the figures, draw the graphs.

    uv run --with boto3 --with matplotlib python aws/monitoring/scripts/report.py
    uv run --with boto3 --with matplotlib python aws/monitoring/scripts/report.py --api data

* ``--api statistics`` (default) reads with ``GetMetricStatistics``,
  ``--api data`` with ``GetMetricData``: same numbers, two different APIs.
* For every phase of the scenario it computes the request count, RPS, error rate
  (Errors / RequestCount) and the mean response time (Duration Sum / SampleCount).
* Prints the alarm state transitions (``DescribeAlarmHistory``) and saves graphs.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

NAMESPACE = "Mycelium/Api"
SYSTEM = [
    {"Name": "Service", "Value": "mycelium-api"},
    {"Name": "Scope", "Value": "all"},
]
PER_INSTANCE = [{"Name": "Service", "Value": "mycelium-api"}]
ROOT = Path(__file__).resolve().parents[1]
DOCS_IMG = Path(__file__).resolve().parents[3] / "docs" / "img"
ALARMS = ["mycelium-errors-high", "mycelium-latency-high"]


# ---- pure functions (unit-tested) -------------------------------------------------


def bucketize(
    points: dict[float, float], start: float, end: float, period: int
) -> list[tuple[float, float]]:
    """Put a (timestamp -> value) series onto a regular grid; gaps become 0.

    Servers align periods to the query's StartTime, not to multiples of the period,
    so every point is snapped to the grid cell that contains it.
    """
    snapped: dict[float, float] = {}
    for ts, value in points.items():
        cell = ts - ts % period
        snapped[cell] = snapped.get(cell, 0.0) + value
    grid = []
    t = start - start % period
    while t < end:
        grid.append((t, snapped.get(t, 0.0)))
        t += period
    return grid


def transitions_from_log(log: list[dict]) -> list[tuple[float, str, str]]:
    """State changes seen while polling (the first sample of each alarm is not a change)."""
    last: dict[str, str] = {}
    changes = []
    for item in sorted(log, key=lambda i: i["t"]):
        previous = last.get(item["alarm"])
        if previous is not None and previous != item["state"]:
            changes.append((item["t"], item["alarm"], item["state"]))
        last[item["alarm"]] = item["state"]
    return changes


def summarize(
    phase: dict, requests, errors, dur_sum, dur_count, margin: float = 0
) -> dict:
    """Figures for one phase from the per-period series (lists of (t, value)).

    ``margin`` trims that many seconds off both ends of the phase: a period that
    straddles two phases would otherwise blend their numbers.
    """
    lo, hi = phase["start"] + margin, phase["end"] - margin

    def total(series):
        return sum(v for t, v in series if lo <= t < hi)

    n = total(requests)
    seconds = hi - lo
    count = total(dur_count)
    return {
        "phase": phase["name"],
        "seconds": round(seconds),
        "requests": int(n),
        "rps": round(n / seconds, 1) if seconds else 0.0,
        "errors": int(total(errors)),
        "error_rate_pct": round(100 * total(errors) / n, 1) if n else 0.0,
        "avg_ms": round(total(dur_sum) / count, 1) if count else 0.0,
    }


# ---- CloudWatch access ----------------------------------------------------------------


def client(endpoint: str):
    import boto3

    return boto3.client(
        "cloudwatch",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def fetch(
    cw, metric, stat, start, end, period, dims=SYSTEM, api="statistics"
) -> dict[float, float]:
    """One statistic of one metric -> {period start (epoch s): value}."""
    start_dt = datetime.fromtimestamp(start, UTC)
    end_dt = datetime.fromtimestamp(end, UTC)
    if api == "data":
        response = cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "q",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": NAMESPACE,
                            "MetricName": metric,
                            "Dimensions": dims,
                        },
                        "Period": period,
                        "Stat": stat,
                    },
                }
            ],
            StartTime=start_dt,
            EndTime=end_dt,
            ScanBy="TimestampAscending",
        )["MetricDataResults"][0]
        return {
            t.timestamp(): v
            for t, v in zip(response["Timestamps"], response["Values"], strict=True)
        }
    points = cw.get_metric_statistics(
        Namespace=NAMESPACE,
        MetricName=metric,
        Dimensions=dims,
        StartTime=start_dt,
        EndTime=end_dt,
        Period=period,
        Statistics=[stat],
    )["Datapoints"]
    return {p["Timestamp"].timestamp(): p[stat] for p in points}


def alarm_history(cw) -> list[tuple[datetime, str, str]]:
    rows = []
    for name in ALARMS:
        items = cw.describe_alarm_history(
            AlarmName=name, HistoryItemType="StateUpdate", MaxRecords=100
        )
        for item in items["AlarmHistoryItems"]:
            rows.append((item["Timestamp"], name, item["HistorySummary"]))
    return sorted(rows)


# ---- output ---------------------------------------------------------------------------------


def draw(grid, phases, transitions, out: Path, period: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t0 = phases[0]["start"]
    xs = [(t - t0) for t, _ in grid["requests"]]
    colors = {
        "baseline": "#e8f0fe",
        "errors": "#fde8e8",
        "latency": "#fff4e0",
        "recovery": "#e6f4ea",
    }
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    rps = [v / period for _, v in grid["requests"]]
    err = [
        100 * e / r if r else 0
        for (_, e), (_, r) in zip(grid["errors"], grid["requests"], strict=True)
    ]
    avg = [
        s / c if c else 0
        for (_, s), (_, c) in zip(grid["dur_sum"], grid["dur_count"], strict=True)
    ]
    for ax, ys, label in zip(
        axes,
        (rps, err, avg),
        ("requests / s", "error rate, %", "mean response time, ms"),
        strict=True,
    ):
        ax.plot(xs, ys, color="#1a56db", linewidth=1.8, marker="o", markersize=3)
        for phase in phases:
            ax.axvspan(
                phase["start"] - t0,
                phase["end"] - t0,
                color=colors.get(phase["name"], "#eee"),
                alpha=0.7,
                zorder=0,
            )
        ax.set_ylabel(label)
        ax.grid(alpha=0.3)
    axes[2].axhline(150, color="#c81e1e", linestyle="--", linewidth=1)
    axes[2].text(xs[0], 155, "alarm threshold 150 ms", color="#c81e1e", fontsize=8)
    for phase in phases:
        axes[0].text(
            phase["start"] - t0 + 1,
            axes[0].get_ylim()[1] * 0.92,
            phase["name"],
            fontsize=9,
        )
    for when, alarm, state in transitions:
        color = "#c81e1e" if state == "ALARM" else "#1e8e3e"
        for ax in axes:
            ax.axvline(when - t0, color=color, alpha=0.7, linewidth=1.2, linestyle="--")
        axes[0].text(
            when - t0 + 1,
            axes[0].get_ylim()[1] * 0.72,
            f"{alarm.replace('mycelium-', '')} -> {state}",
            fontsize=7,
            color=color,
            rotation=90,
            va="top",
        )
    axes[2].set_xlabel("seconds since the start of the scenario")
    fig.suptitle(
        "Mycelium API: CloudWatch metrics under controlled load and injected faults"
    )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)


def draw_instances(per_instance, phases, out: Path, period: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t0 = phases[0]["start"]
    fig, ax = plt.subplots(figsize=(11, 4))
    for name, series in sorted(per_instance.items()):
        ax.plot(
            [t - t0 for t, _ in series],
            [v / period for _, v in series],
            marker="o",
            markersize=3,
            label=name,
        )
    for phase in phases:
        ax.axvline(phase["start"] - t0, color="#999", linestyle=":")
    ax.set_xlabel("seconds since the start of the scenario")
    ax.set_ylabel("requests / s")
    ax.set_title("Requests handled per instance (dimension InstanceId)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=130)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=str(ROOT / "results" / "scenario.json"))
    parser.add_argument("--endpoint", default="http://localhost:4566")
    parser.add_argument("--api", choices=["statistics", "data"], default="statistics")
    parser.add_argument("--period", type=int, default=10)
    parser.add_argument("--out", default=str(DOCS_IMG))
    args = parser.parse_args()

    scenario = json.loads(Path(args.scenario).read_text())
    period = args.period
    start = int((scenario["start"] - 15) // period * period)  # aligned to the grid
    end = scenario["end"] + 15
    cw = client(args.endpoint)

    def series(metric, stat, dims=SYSTEM):
        return bucketize(
            fetch(cw, metric, stat, start, end, period, dims, args.api),
            start,
            end,
            period,
        )

    grid = {
        "requests": series("RequestCount", "Sum"),
        "errors": series("Errors", "Sum"),
        "dur_sum": series("Duration", "Sum"),
        "dur_count": series("Duration", "SampleCount"),
    }
    print(
        f"metrics read with {'GetMetricData' if args.api == 'data' else 'GetMetricStatistics'}, period {period}s\n"
    )
    rows = [
        summarize(
            p,
            grid["requests"],
            grid["errors"],
            grid["dur_sum"],
            grid["dur_count"],
            margin=period,
        )
        for p in scenario["phases"]
    ]
    print(
        f"(each phase is trimmed by {period}s at both ends: no period blends two phases)\n"
    )
    header = "| phase | seconds | requests | RPS | 5xx errors | error rate % | mean response ms |"
    lines = [header, "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(
            f"| {r['phase']} | {r['seconds']} | {r['requests']} | {r['rps']} | {r['errors']} | {r['error_rate_pct']} | {r['avg_ms']} |"
        )
    table = "\n".join(lines)
    print(table)

    history = [
        row for row in alarm_history(cw) if row[0].timestamp() >= scenario["start"] - 60
    ]  # only this run
    print("\nalarm state transitions (DescribeAlarmHistory):")
    for when, name, summary in history:
        print(f"  {when:%H:%M:%S}  {name:<22} {summary}")
    if scenario["alarm_log"]:
        print("\nalarm states seen while the scenario ran (polled every 5 s):")
        t0 = scenario["start"]
        for item in scenario["alarm_log"]:
            print(f"  +{item['t'] - t0:5.0f}s  {item['alarm']:<22} {item['state']}")

    per_instance = {}
    for instance in ("app-1", "app-2", "app-3"):
        dims = PER_INSTANCE + [{"Name": "InstanceId", "Value": instance}]
        per_instance[instance] = bucketize(
            fetch(cw, "RequestCount", "Sum", start, end, period, dims, args.api),
            start,
            end,
            period,
        )

    out = Path(args.out)
    transitions = transitions_from_log(scenario["alarm_log"])
    draw(grid, scenario["phases"], transitions, out / "lab10-metrics.png", period)
    draw_instances(
        per_instance, scenario["phases"], out / "lab10-instances.png", period
    )
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "summary.md").write_text(table + "\n")
    print(f"\ngraphs: {out / 'lab10-metrics.png'}, {out / 'lab10-instances.png'}")


if __name__ == "__main__":
    main()
