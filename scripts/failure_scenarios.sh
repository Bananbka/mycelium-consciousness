#!/usr/bin/env bash
# Lab 10: break one component under load and record what the clients see.
#   scripts/failure_scenarios.sh instance|redis|replica|shard|primary [A|B|C]
# k6 runs 10 s warm-up + 40 s; the component is stopped 15 s into the measured
# phase and started again 15 s later. Results: load-tests/results/l10_fail_<what>_<scenario>.json
# COMPOSE, NETWORK, ENV_FILE as in profiling/run.sh.
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
NET="${NETWORK:-infra_default}"
what="${1:?instance|redis|replica|shard|primary}"
scen="${2:-A}"
name="l10_fail_${what}_${scen}${SUFFIX:-}"

case "$what" in
instance) target="$($C ps -q api | head -1)"; stop() { docker stop -t 1 "$target" >/dev/null; }; start() { docker start "$target" >/dev/null; } ;;
redis)    stop() { $C stop -t 1 redis >/dev/null; };      start() { $C start redis >/dev/null; } ;;
replica)  stop() { $C stop -t 1 db-replica >/dev/null; }; start() { $C start db-replica >/dev/null; } ;;
shard)    stop() { $C stop -t 1 shard-b >/dev/null; };    start() { $C start shard-b >/dev/null; } ;;
primary)  stop() { $C stop -t 1 db >/dev/null; };         start() { $C start db >/dev/null; } ;;
*) echo "unknown: $what" >&2; exit 2 ;;
esac

docker run --rm -i --cpus=2 --network "$NET" -v "$PWD/load-tests:/lt" grafana/k6:latest run --quiet \
  -e SCENARIO="$scen" -e NOCACHE="${NOCACHE:-0}" -e VUS=50 -e DURATION=40s -e WARMUP=10s -e BASE_URL=http://nginx \
  --summary-export="/lt/results/$name.json" /lt/mycelium.js >/dev/null 2>"load-tests/results/$name.err" &
K6=$!
sleep 25    # 10 s warm-up + 15 s steady
echo "[$(date +%T)] stopping $what" >&2; stop
sleep 15
echo "[$(date +%T)] starting $what" >&2; start
wait $K6 || true
python load-tests/scrub.py "load-tests/results/$name.json"
rm -f "load-tests/results/$name.err"
sleep 15    # let the component settle before the next scenario
