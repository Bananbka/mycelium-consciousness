#!/usr/bin/env bash
# Lab 7 experiments against the running stack (control-plane db + shard-a/shard-b).
#   scripts/shard_experiments.sh model         # offline: distribution + resharding model (no stack needed)
#   scripts/shard_experiments.sh distribution  # live Data Distribution Matrix for the active strategy
#   scripts/shard_experiments.sh hotshard      # one big tenant under modulo -> hash -> vbucket
#   scripts/shard_experiments.sh fanout        # single node vs sharded: shard-local vs cross-shard reads
#
# COMPOSE overrides the compose invocation, ENV_FILE the env file, BASE the API URL.
# Switching strategy/topology recreates api + celery containers; data placed
# under one strategy is not re-homed (that is what resharding is for).
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
export COMPOSE="$C"
export BASE="${BASE:-http://localhost}"
set -a; . <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${ENV_FILE:-infra/.env}"); set +a
NODES="a=shard-a:5432,b=shard-b:5432"
LOADER="uv run --with httpx python tools/shard_load.py"

apply() { # SHARD_NODES SHARD_STRATEGY
  echo ">>> topology: nodes='${1:-<single db>}' strategy=$2" >&2
  SHARD_NODES="$1" SHARD_STRATEGY="$2" $C up -d --no-deps --scale api=2 api celery-worker celery-beat >/dev/null 2>&1
  sleep 10
  $C restart nginx >/dev/null 2>&1
  sleep 5
}

# Rows left by earlier runs would make later topologies scan more data than
# earlier ones. Destructive, so opt-in: RESET_DATA_PLANE=1.
reset_data_plane() {
  [ "${RESET_DATA_PLANE:-0}" = 1 ] || return 0
  echo ">>> truncating memory_buffers/memory_backups on db, shard-a, shard-b" >&2
  for svc in db shard-a shard-b; do
    $C exec -T "$svc" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qc \
      "TRUNCATE memory_backups, memory_buffers" >/dev/null
  done
}

ensure_admin() {
  export ADMIN_EMAIL="${ADMIN_EMAIL:-shard-ops@example.com}"
  export ADMIN_PASSWORD="${ADMIN_PASSWORD:-ShardOps-123-x}"
  $C exec -T api uv run --package api --frozen --no-sync seed-admin \
    "$ADMIN_EMAIL" "$ADMIN_PASSWORD" >/dev/null 2>&1 || true
}

case "${1:-model}" in
model)
  uv run --with matplotlib python tools/shard_model.py "${@:2}" ;;

distribution)
  ensure_admin
  $LOADER distribution --clones "${CLONES:-120}" --wait "${WAIT:-150}" ;;

hotshard)
  ensure_admin
  for strategy in ${STRATEGIES:-modulo hash vbucket}; do
    apply "$NODES" "$strategy"
    $LOADER hot --label "$strategy" --clones "${CLONES:-40}" \
      --duration "${DURATION:-30}" --concurrency "${CONCURRENCY:-24}"
  done
  apply "$NODES" vbucket ;;

fanout)
  ensure_admin
  reset_data_plane
  apply "" vbucket
  $LOADER fanout --label single --clones "${CLONES:-40}" --duration "${DURATION:-30}" \
    --concurrency "${CONCURRENCY:-24}"
  reset_data_plane
  apply "$NODES" vbucket
  $LOADER fanout --label sharded --clones "${CLONES:-40}" --duration "${DURATION:-30}" \
    --concurrency "${CONCURRENCY:-24}" ;;

*) echo "usage: $0 model|distribution|hotshard|fanout" >&2; exit 2 ;;
esac
