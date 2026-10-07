#!/usr/bin/env bash
# Lab 6 experiments. Run from the repo root with the stack already up
# (docker compose -f infra/docker-compose.yaml up -d --build).
#   scripts/lb_experiments.sh distribution   # round robin, 20 sequential requests
#   scripts/lb_experiments.sh failover       # stop one replica, watch routing
#   scripts/lb_experiments.sh algorithms     # RR / least_conn / weighted / ip_hash, fast + slow node
#   scripts/lb_experiments.sh compare        # Locust: 1 replica vs 3 replicas
#   scripts/lb_experiments.sh scaleout       # closed-loop bench, 1 / 2 / 3 replicas
#   scripts/lb_experiments.sh failover-load  # kill a replica mid-load (lb_bench)
set -euo pipefail
PROJECT="${PROJECT:-clone-sandbox}"
C="docker compose -f infra/docker-compose.yaml"
BENCH="env MSYS_NO_PATHCONV=1 uv run --with httpx python tools/lb_bench.py"
DUR="${DUR:-20}"
CONC="${CONC:-30}"
BASE_SRV="server api:8000 max_fails=2 fail_timeout=10s;"

set_lb() { # algorithm, servers
  export LB_ALGORITHM="$1" LB_SERVERS="$2"
  $C up -d --no-deps --force-recreate nginx >/dev/null 2>&1
  wait_ready
}
wait_ready() { for _ in $(seq 1 30); do curl -sf -o /dev/null http://localhost/health && return; sleep 1; done; }
scale() { $C up -d --no-deps --scale api="$1" api >/dev/null 2>&1; sleep 8; $C restart nginx >/dev/null 2>&1; wait_ready; }
seq_req() { # n requests, sequential: "instance upstream_ip" per line
  for _ in $(seq 1 "$1"); do
    curl -s -o /dev/null -D - http://localhost/health | tr -d '\r' \
      | awk -F': ' 'tolower($1)=="x-instance-id"{i=$2} tolower($1)=="x-upstream-addr"{u=$2} END{print i, u}'
  done
}
count() { sort | uniq -c; }

case "${1:-}" in
distribution)
  scale 3; set_lb "" "$BASE_SRV"
  echo "### round robin, 3 replicas, 20 sequential requests (order of replies)"
  out=$(seq_req 20); echo "$out" | nl; echo "--- per replica"; echo "$out" | count ;;
failover)
  scale 3; set_lb "" "$BASE_SRV"
  echo "### before: 9 requests"; seq_req 9 | count
  echo ">>> docker stop ${PROJECT}-api-2"; docker stop "${PROJECT}-api-2" >/dev/null
  echo "### after stop: 12 requests (no errors expected, only 2 replicas answer)"
  for _ in $(seq 1 12); do curl -s -o /dev/null -w '%{http_code} ' http://localhost/health; done; echo
  seq_req 12 | count
  echo ">>> docker start ${PROJECT}-api-2"; docker start "${PROJECT}-api-2" >/dev/null
  sleep 15
  echo "### after start (fail_timeout expired): 12 requests"; seq_req 12 | count ;;
algorithms)
  scale 1
  $C --profile slow up -d --no-deps api-slow >/dev/null 2>&1; sleep 8
  SLOW="server api:8000; server api-slow:8000;"
  W="server api:8000 weight=3; server api-slow:8000 weight=1;"
  for cfg in "|$SLOW|round_robin" "least_conn;|$SLOW|least_conn" "|$W|weighted_rr(3:1)" "ip_hash;|$SLOW|ip_hash"; do
    IFS='|' read -r algo srv name <<<"$cfg"
    set_lb "$algo" "$srv"; echo "### $name  (fast api + api-slow, ${SLOW_LATENCY_MS:-300}ms)"
    $BENCH --duration "$DUR" --concurrency "$CONC"
  done
  $C --profile slow stop api-slow >/dev/null 2>&1 ;;
compare)
  mkdir -p results
  for n in 1 3; do
    scale "$n"; set_lb "" "$BASE_SRV"; echo "### $n replica(s), Locust profile lb"
    loadtests/run.sh lb http://localhost "$PROJECT" >"results/lb_${n}_replicas.log" 2>&1 || true
    for f in results/lb_stats.csv; do cp "$f" "results/lb_${n}_replicas_stats.csv"; done
    grep -E '^(,|")?Aggregated|^Type|Aggregated' "results/lb_${n}_replicas_stats.csv" || true
  done ;;
scaleout)
  for n in 1 2 3; do
    scale "$n"; set_lb "" "$BASE_SRV"; echo "### $n replica(s), write path"
    $BENCH --duration "$DUR" --concurrency "$CONC" --method POST --path /memories/write
    echo "--- read path"; $BENCH --duration "$DUR" --concurrency "$CONC"
  done ;;
failover-load)
  scale 3; set_lb "" "$BASE_SRV"
  ( sleep 8; echo ">>> docker stop ${PROJECT}-api-2"; docker stop "${PROJECT}-api-2" >/dev/null ) &
  $BENCH --duration 25 --concurrency 20 --method POST --path /memories/write
  wait; docker start "${PROJECT}-api-2" >/dev/null ;;
*) sed -n 2,9p "$0" ;;
esac
