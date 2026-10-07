#!/usr/bin/env bash
# Lab 7: measure one optimization stage from a clean stack.
#   scripts/run_stage.sh <stage-name> [profiles...]     (default: baseline step)
# Recreates the sandbox with API_REPLICAS api replicas (default 1, as in lab 4;
# COMPOSE_FILES adds overlays, e.g. the lab 8 monitoring stack), runs the lab 4
# Locust profiles through nginx, and files the raw CSVs under results/<stage>/.
set -euo pipefail
STAGE="${1:?stage name, e.g. s0-before}"
shift || true
PROFILES=("${@:-baseline step}")
read -r -a PROFILES <<<"${PROFILES[*]}"
C="docker compose ${COMPOSE_FILES:--f infra/docker-compose.yaml}"
PROJECT="${PROJECT:-clone-sandbox}"

$C --profile slow down -v --remove-orphans >/dev/null 2>&1
API_REPLICAS="${API_REPLICAS:-1}" $C up -d --build >/dev/null 2>&1
for _ in $(seq 1 60); do curl -sf -o /dev/null http://localhost/health && break; sleep 2; done
sleep 10 # let celery/minio settle

mkdir -p "results/$STAGE"
for p in "${PROFILES[@]}"; do
  loadtests/run.sh "$p" http://localhost "$PROJECT" >"results/$STAGE/$p.log" 2>&1 || true
  sleep 10 # the docker-stats collector releases its CSV a moment after Locust exits
  mv results/"$p"_* results/"$p".html "results/$STAGE/"
  sleep 20
done
echo "done: results/$STAGE"
