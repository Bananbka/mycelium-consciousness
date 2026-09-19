#!/usr/bin/env bash
# Lab 5 runner. Usage: scripts/run_load_tests.sh <matrix|scaling|cache|prep>
# Results land in load-tests/results/*.json (k6 --summary-export).
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="docker compose -f infra/docker-compose.yaml"
set -a; . infra/.env; set +a
DURATION="${DURATION:-30s}"

psql_() { $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
rows_read() { psql_ "SELECT tup_returned+tup_fetched FROM pg_stat_database WHERE datname='$POSTGRES_DB'"; }
backups_rows() { psql_ "SELECT seq_tup_read+COALESCE(idx_tup_fetch,0) FROM pg_stat_user_tables WHERE relname='memory_backups'"; }
scale() {
  $C up -d --no-deps --scale api="$1" api >/dev/null 2>&1; sleep 8
  $C restart nginx >/dev/null 2>&1; sleep 4
}
k6run() { # name scenario vus [extra -e args]
  local name="$1" scen="$2" vus="$3"; shift 3
  echo ">>> $name (scenario $scen, $vus VUs, ${DURATION})"
  local before after bbefore bafter; before=$(rows_read); bbefore=$(backups_rows)
  ( sleep 25; docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}'       > "load-tests/results/$name.stats" 2>/dev/null ) &
  docker run --rm -i --cpus=2 --network infra_default \
    -v "$PWD/load-tests:/lt" grafana/k6:latest run --quiet \
    -e SCENARIO="$scen" -e VUS="$vus" -e DURATION="$DURATION" "$@" \
    --summary-export="/lt/results/$name.json" /lt/mycelium.js >/dev/null 2>"load-tests/results/$name.err" || true
  wait
  python load-tests/scrub.py "load-tests/results/$name.json"  # drop auth tokens from the export
  sleep 2  # let pg_stat_* flush
  after=$(rows_read); echo "$((after-before))" > "load-tests/results/$name.dbrows"
  bafter=$(backups_rows); echo "$((bafter-bbefore))" > "load-tests/results/$name.backupsrows"
}

case "${1:?matrix|scaling|cache|prep}" in
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
scaling)  # 1 vs 3 instances (2 comes from the matrix), 100 VUs
  for n in 1 3; do scale "$n"
    for scen in A B; do k6run "scale${n}_${scen}_100" "$scen" 100; done
  done ;;
cache)    # scenario A with the cache bypassed, 100 VUs, 2 instances
  scale 2; k6run "nocache_A_100" A 100 -e NOCACHE=1 ;;
esac
