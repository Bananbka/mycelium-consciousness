"""Sample CPU and memory of every container in a compose project into a CSV.

    uv run python loadtests/collect_docker_stats.py clone-sandbox results/step_docker.csv

Runs until interrupted (Ctrl+C / SIGTERM). Locust measures the client side; this
is the server side: which service saturates first.
"""

from __future__ import annotations

import csv
import json
import re
import signal
import subprocess
import sys
import time

_UNITS = {
    "B": 1,
    "KB": 1e3,
    "MB": 1e6,
    "GB": 1e9,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}


def _bytes(text: str) -> float:
    match = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    return float(match[1]) * _UNITS[match[2].upper()] if match else 0.0


def _containers(project: str) -> list[str]:
    out = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def main() -> None:
    project, path = sys.argv[1], sys.argv[2]
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    start = time.time()
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["elapsed_s", "container", "cpu_percent", "mem_mib"])
        while not stop:
            names = _containers(project)
            if names:
                out = subprocess.run(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        *names,
                    ],
                    capture_output=True,
                    text=True,
                ).stdout
                elapsed = round(time.time() - start, 1)
                for line in out.splitlines():
                    row = json.loads(line)
                    service = row["Name"].removeprefix(f"{project}-").rsplit("-", 1)[0]
                    writer.writerow(
                        [
                            elapsed,
                            service,
                            row["CPUPerc"].rstrip("%"),
                            round(_bytes(row["MemUsage"].split("/")[0]) / 1024**2, 1),
                        ]
                    )
                handle.flush()


if __name__ == "__main__":
    main()
