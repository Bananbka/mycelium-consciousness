#!/usr/bin/env bash
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
NET="${NETWORK:-infra_default}"
set -a; . <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${ENV_FILE:-infra/.env}"); set +a  # tolerate BOM/CRLF
DURATION="${DURATION:-30s}"

psql_() { $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
rpsql_() { $C exec -T db-replica psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
replica_rows_read() { rpsql_ "SELECT tup_returned+tup_fetched FROM pg_stat_database WHERE datname='$POSTGRES_DB'" 2>/dev/null || echo 0; }
shard_rows() { # "a:N b:N" - rows read on each shard (empty when unsharded)
  local out=""
  for svc in shard-a shard-b; do
    v=$($C exec -T "$svc" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT tup_returned+tup_fetched FROM pg_stat_database WHERE datname='$POSTGRES_DB'" 2>/dev/null || echo 0)
    out="$out ${svc#shard-}:${v:-0}"
  done
  echo "$out"
}
rows_read() { psql_ "SELECT tup_returned+tup_fetched FROM pg_stat_database WHERE datname='$POSTGRES_DB'"; }
backups_rows() { psql_ "SELECT seq_tup_read+COALESCE(idx_tup_fetch,0) FROM pg_stat_user_tables WHERE relname='memory_backups'"; }
scale() {
  $C up -d --no-deps --scale api="$1" api >/dev/null 2>&1; sleep 8
  $C restart nginx >/dev/null 2>&1; sleep 4
}
k6run() { # name scenario vus [extra -e args]
  local name="$1" scen="$2" vus="$3"; shift 3
  echo ">>> $name (scenario $scen, $vus VUs, ${DURATION})"
  local before after bbefore bafter rbefore sbefore; before=$(rows_read); bbefore=$(backups_rows); rbefore=$(replica_rows_read); sbefore=$(shard_rows)
  ( sleep 25; docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}'       > "load-tests/results/$name.stats" 2>/dev/null ) &
  docker run --rm -i --cpus=2 --network "$NET" \
    -v "$PWD/load-tests:/lt" grafana/k6:latest run --quiet \
    -e SCENARIO="$scen" -e VUS="$vus" -e DURATION="$DURATION" "$@" \
    --summary-export="/lt/results/$name.json" /lt/mycelium.js >/dev/null 2>"load-tests/results/$name.err" || true
  wait
  python load-tests/scrub.py "load-tests/results/$name.json"  # drop auth tokens from the export
  sleep 2  # let pg_stat_* flush
  after=$(rows_read); echo "$((after-before))" > "load-tests/results/$name.dbrows"
  echo "$(( $(replica_rows_read) - rbefore ))" > "load-tests/results/$name.replicarows"
  sleep 12  # per-table counters are published lazily by idle backends
  echo "before:$sbefore after:$(shard_rows)" > "load-tests/results/$name.shardrows"
  bafter=$(backups_rows); echo "$((bafter-bbefore))" > "load-tests/results/$name.backupsrows"
}

case "${1:?matrix|scaling|cache|replica|shard|prep}" in
prep)
  scale 2
  k6run prep PREP 1
  psql_ "INSERT INTO memory_backups (clone_id, period_start, period_end, entry_count, storage_key, subscription_tier)
         SELECT p.id, now() - (g || ' hours')::interval, now() - (g || ' hours')::interval, 10,
                'seed/' || p.id || '/' || g, 'free'
         FROM clone_profiles p, generate_series(1,50) g
         WHERE p.designation LIKE 'lt-clone-%'
           AND NOT EXISTS (SELECT 1 FROM memory_backups b WHERE b.clone_id = p.id)" >/dev/null
  echo "seeded: $(psql_ "SELECT count(*) FROM memory_backups b JOIN clone_profiles p ON p.id=b.clone_id WHERE p.designation LIKE 'lt-clone-%'") backups" ;;
matrix)   # 3 scenarios x 4 load levels, 2 api instances
  scale 2
  for scen in A B C; do for vus in 10 50 100 200; do k6run "matrix_${scen}_${vus}" "$scen" "$vus"; done; done ;;
extra)    # finer low-load level + re-measure cache runs with per-table counters + container CPU
  scale 2
  for scen in A B C; do k6run "matrix_${scen}_25" "$scen" 25; done
  k6run "matrix_A_100" A 100
  k6run "nocache_A_100" A 100 -e NOCACHE=1
  k6run "matrix_B_100" B 100 ;;
low)      # resolve the saturation knee: very low load levels
  scale 2
  for scen in A B C; do for vus in 2 5; do k6run "matrix_${scen}_${vus}" "$scen" "$vus"; done; done ;;
ramp)     # one live staged run 10->25->50->100->200 VUs (defence demo), scenario B by default
  scale 2; k6run "ramp_${SCEN:-B}" "${SCEN:-B}" 200 -e RAMP=1 -e STAGE="${STAGE:-30s}" ;;
scaling)  # 1 vs 3 instances (2 comes from the matrix), 100 VUs
  for n in 1 3; do scale "$n"
    for scen in A B; do k6run "scale${n}_${scen}_100" "$scen" 100; done
  done ;;
replica)  # lab 6: read scaling, scenario A with the cache bypassed so every read reaches Postgres.
          # "single" = POSTGRES_REPLICA_HOST empty (all traffic on the primary); "replica" = split.
  for cfg in single replica; do
    host=""; [ "$cfg" = replica ] && host="db-replica"
    POSTGRES_REPLICA_HOST="$host" $C up -d --no-deps --scale api=2 api >/dev/null 2>&1; sleep 8
    $C restart nginx >/dev/null 2>&1; sleep 4
    for vus in ${LEVELS:-10 50 100 200}; do
      k6run "l6_${cfg}_A_${vus}" A "$vus" -e NOCACHE=1
    done
  done
  POSTGRES_REPLICA_HOST="db-replica" $C up -d --no-deps --scale api=2 api >/dev/null 2>&1 ;;
shard)    # lab 7: single database vs 2 shards. A = shard-local read (cache bypassed),
          # E = cross-shard fan-out (needs ADMIN_EMAIL / ADMIN_PASSWORD).
  : "${ADMIN_EMAIL:?set ADMIN_EMAIL}" "${ADMIN_PASSWORD:?set ADMIN_PASSWORD}"
  reset_plane() { # RESET_DATA_PLANE=1: stale rows would skew single-vs-sharded scan sizes
    [ "${RESET_DATA_PLANE:-0}" = 1 ] || return 0
    for svc in db shard-a shard-b; do
      $C exec -T "$svc" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qc \
        "TRUNCATE memory_backups, memory_buffers" >/dev/null
    done
  }
  for topo in single sharded; do
    nodes=""; [ "$topo" = sharded ] && nodes="a=shard-a:5432,b=shard-b:5432"
    reset_plane
    SHARD_NODES="$nodes" $C up -d --no-deps --scale api=2 api celery-worker celery-beat >/dev/null 2>&1
    sleep 10; $C restart nginx >/dev/null 2>&1; sleep 4
    uv run --with httpx python tools/shard_load.py seed --backups 50 >/dev/null
    for vus in ${LEVELS:-10 50 100 200}; do
      k6run "l7_${topo}_A_${vus}" A "$vus" -e NOCACHE=1
      k6run "l7_${topo}_E_${vus}" E "$vus" -e ADMIN_EMAIL="$ADMIN_EMAIL" -e ADMIN_PASSWORD="$ADMIN_PASSWORD"
    done
  done
  SHARD_NODES="a=shard-a:5432,b=shard-b:5432" $C up -d --no-deps --scale api=2 api celery-worker celery-beat >/dev/null 2>&1 ;;
cache)    # scenario A with the cache bypassed, 100 VUs, 2 instances
  scale 2; k6run "nocache_A_100" A 100 -e NOCACHE=1 ;;
esac
