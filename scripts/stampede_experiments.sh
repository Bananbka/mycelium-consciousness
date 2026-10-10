#!/usr/bin/env bash
# Lab 8 experiments: cache stampede, avalanche, penetration (see tools/stampede.py).
#   scripts/stampede_experiments.sh seed         # give the leaderboard something expensive to aggregate
#   scripts/stampede_experiments.sh stampede     # 500 VUs on a just-expired key: none vs lock vs swr
#   scripts/stampede_experiments.sh avalanche    # 60 keys, same TTL: jitter 0 vs 0.3 (and the lock)
#   scripts/stampede_experiments.sh penetration  # missing ids: none / negative / bloom / both
#
# COMPOSE overrides the compose invocation, ENV_FILE the env file, BASE the API URL.
# The api containers are recreated with ALLOW_PROTECTION_OVERRIDE=1 and a 10 s TTL.
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
export COMPOSE="$C"
export BASE="${BASE:-http://localhost}"
set -a; . <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${ENV_FILE:-infra/.env}"); set +a
export ADMIN_EMAIL="${ADMIN_EMAIL:-cache-ops@example.com}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-CacheOps-123-x}"
TOOL="uv run --with httpx python tools/stampede.py"
TTL="${LEADERBOARD_TTL_SECONDS:-10}"

apply() { # CACHE_TTL_JITTER [CACHE_XFETCH_BETA]
  echo ">>> api: TTL=${TTL}s jitter=$1" >&2
  CACHE_TTL_JITTER="$1" LEADERBOARD_TTL_SECONDS="$TTL" ALLOW_PROTECTION_OVERRIDE=1 \
    LEADERBOARD_SIMULATED_QUERY_MS="${COST_MS:-0}" \
    $C up -d --no-deps --scale api=2 api >/dev/null 2>&1
  sleep 10
  $C restart nginx >/dev/null 2>&1
  # wait until the recreated api answers through nginx (a 502 here would abort a run)
  for _ in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/health" || true)" = 200 ] && break
    sleep 2
  done
  sleep 3
}

ensure_admin() {
  $C exec -T api uv run --package api --frozen --no-sync seed-admin \
    "$ADMIN_EMAIL" "$ADMIN_PASSWORD" >/dev/null 2>&1 || true
}

case "${1:-stampede}" in
seed)
  ensure_admin
  $TOOL seed --clones "${CLONES:-60}" --backups "${BACKUPS:-1500}" ;;

stampede)
  ensure_admin
  apply 0.1
  for mode in none lock swr; do
    $TOOL stampede --mode "$mode" --vus "${VUS:-500}"
  done ;;

avalanche)
  ensure_admin
  for jitter in 0 0.3; do
    apply "$jitter"
    for mode in none lock; do
      $TOOL avalanche --mode "$mode" --label "jitter${jitter}" --ttl "$TTL" \
        --keys "${KEYS:-60}" --duration "${DURATION:-28}" --rps "${RPS:-80}"
    done
  done ;;

penetration)
  ensure_admin
  apply 0.1
  for pattern in same distinct; do
    for guard in none negative bloom both; do
      $TOOL penetration --guard "$guard" --pattern "$pattern" --requests "${REQUESTS:-3000}"
    done
  done ;;

*) echo "usage: $0 seed|stampede|avalanche|penetration" >&2; exit 2 ;;
esac
