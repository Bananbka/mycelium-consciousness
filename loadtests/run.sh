#!/usr/bin/env bash
# Run one named profile against a deployed sandbox and collect client + server metrics.
#   loadtests/run.sh baseline|step [host] [compose-project]
set -euo pipefail

PROFILE="${1:?profile: baseline|step}"
HOST="${2:-http://localhost:8088}"
PROJECT="${3:-clone-sandbox}"
OUT="results/${PROFILE}"
mkdir -p results

ACCOUNTS=$(PYTHONPATH=loadtests uv run --group load python -c "from profiles import PROFILES; print(PROFILES[\"$PROFILE\"].max_users)")
uv run --group load python loadtests/provision.py "$HOST" "$ACCOUNTS" results/accounts.json

uv run --group load python loadtests/collect_docker_stats.py "$PROJECT" "${OUT}_docker.csv" &
STATS_PID=$!
trap 'kill "$STATS_PID" 2>/dev/null || true' EXIT

LOAD_PROFILE="$PROFILE" uv run --group load locust -f loadtests/locustfile.py \
  --headless --host "$HOST" --csv "$OUT" --html "${OUT}.html"
