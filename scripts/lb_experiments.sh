#!/usr/bin/env bash
# Lab 3 experiments. Run from the repo root with the stack already up.
#   scripts/lb_experiments.sh distribution   # RR vs least_conn, 2 equal replicas
#   scripts/lb_experiments.sh asymmetric     # 1 fast + 1 slow node, 4 algorithms
#   scripts/lb_experiments.sh scaleout       # 1 / 2 / 3 replicas
#   scripts/lb_experiments.sh failover       # kill a node mid-load
set -euo pipefail
export MSYS_NO_PATHCONV=1
C="docker compose -f infra/docker-compose.yaml"
BENCH="uv run --with httpx python tools/lb_bench.py"
DUR="${DUR:-20}"
CONC="${CONC:-30}"
BASE_SRV="server api:8000 max_fails=3 fail_timeout=15s;"

set_lb() { # algorithm, servers
  export LB_ALGORITHM="$1" LB_SERVERS="$2"
  $C up -d --no-deps --force-recreate nginx >/dev/null 2>&1
  sleep 3
}
scale() { $C up -d --no-deps --scale api="$1" api >/dev/null 2>&1; sleep 8; $C restart nginx >/dev/null 2>&1; sleep 3; }
dist() { # n requests, sequential, count per instance
  for _ in $(seq 1 "$1"); do curl -s -o /dev/null -D - http://localhost/health | tr -d '\r' | grep -i '^x-instance-id'; done | sort | uniq -c
}

case "${1:-}" in
distribution)
  scale 2
  for algo in "" "least_conn;"; do
    set_lb "$algo" "$BASE_SRV"; echo "### algorithm='${algo:-round_robin}' (200 sequential requests)"; dist 200
  done ;;
asymmetric)
  scale 1
  $C --profile slow up -d --no-deps api-slow >/dev/null 2>&1; sleep 8
  SLOW="server api:8000; server api-slow:8000;"
  W="server api:8000 weight=3; server api-slow:8000 weight=1;"
  for cfg in "|$SLOW|round_robin" "least_conn;|$SLOW|least_conn" "|$W|weighted_rr(3:1)" "ip_hash;|$SLOW|ip_hash"; do
    IFS='|' read -r algo srv name <<<"$cfg"
    set_lb "$algo" "$srv"; echo "### $name  (fast api + api-slow, ${SLOW_LATENCY_MS:-300}ms)"
    $BENCH --duration "$DUR" --concurrency "$CONC"
  done ;;
scaleout)
  $C --profile slow stop api-slow >/dev/null 2>&1 || true
  for n in 1 2 3; do
    scale "$n"; set_lb "least_conn;" "$BASE_SRV"; echo "### $n instance(s), write path"
    $BENCH --duration "$DUR" --concurrency "$CONC" --method POST --path /memories/write
    echo "--- read path"; $BENCH --duration "$DUR" --concurrency "$CONC"
  done ;;
failover)
  scale 2; set_lb "least_conn;" "$BASE_SRV"
  ( sleep 8; echo ">>> docker stop infra-api-2"; docker stop infra-api-2 >/dev/null ) &
  $BENCH --duration 25 --concurrency 20 --method POST --path /memories/write
  wait; docker start infra-api-2 >/dev/null ;;
*) sed -n 2,6p "$0" ;;
esac
