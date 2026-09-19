#!/usr/bin/env bash
# Lab 4 experiments against the running stack.
#   scripts/cache_experiment.sh demo   # MISS -> HIT -> invalidation -> Redis-down fallback
#   scripts/cache_experiment.sh bench  # cold (no cache) vs warm, latency + DB load
export MSYS_NO_PATHCONV=1
set -euo pipefail
C="docker compose -f infra/docker-compose.yaml"
BASE=http://localhost
EMAIL="cache-$(date +%s)@example.com"
PASS="$(python -c 'import uuid;print(uuid.uuid4().hex)')"
JSON='Content-Type: application/json'

curl -s -X POST $BASE/auth/register -H "$JSON" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"designation\":\"c-$$\"}" >/dev/null
TOKEN=$(curl -s -X POST $BASE/auth/login -H "$JSON" -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"
PID=$(curl -s $BASE/profiles/me -H "$AUTH" | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
psql_() { $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
read_backups() { curl -s -o /dev/null -D - -w "time=%{time_total}s\n" "$BASE/memories/backups?limit=${1:-50}" -H "$AUTH" | tr -d '\r' | grep -iE "^x-cache|^time|^HTTP" || true; }

set -a; . infra/.env; set +a
psql_ "INSERT INTO memory_backups (clone_id, period_start, period_end, entry_count, storage_key, subscription_tier)
       SELECT $PID, now() - (g || ' hours')::interval, now() - (g || ' hours')::interval, 10,
              'seed/$PID/' || g, 'free' FROM generate_series(1,200) g" >/dev/null

# One backup gets a real object so /restore can actually fetch it.
$C exec -T api uv run --package api --frozen --no-sync python -c "
import asyncio
from datetime import datetime, timezone
from shared import backup_codec, object_storage
async def main():
    await object_storage.put_object('seed/$PID/real', backup_codec.encode_payload(
        [{'content': 'seed', 'captured_at': datetime.now(timezone.utc).isoformat()}]))
asyncio.run(main())"
psql_ "UPDATE memory_backups SET storage_key='seed/$PID/real' WHERE id=(SELECT min(id) FROM memory_backups WHERE clone_id=$PID)" >/dev/null

case "${1:-demo}" in
demo)
  echo "== 1st read (expect MISS)"; read_backups
  echo "== 2nd read (expect HIT)";  read_backups
  echo "== restore a backup -> invalidates"
  BID=$(psql_ "SELECT id FROM memory_backups WHERE clone_id=$PID ORDER BY id LIMIT 1")
  echo "restore HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/memories/backups/$BID/restore" -H "$AUTH")"
  echo "== read after mutation (expect MISS)"; read_backups
  echo "== read again (expect HIT)"; read_backups
  echo "== docker stop redis -> graceful degradation"
  $C stop redis >/dev/null; sleep 2
  read_backups; read_backups
  $C start redis >/dev/null; sleep 5
  echo "== redis back"; read_backups; read_backups ;;
bench)
  DB_XACT() { psql_ "SELECT tup_returned+tup_fetched FROM pg_stat_database WHERE datname='$POSTGRES_DB'"; }
  for mode in cold warm; do
    flag=""; [ "$mode" = cold ] && flag="--no-cache"
    before=$(DB_XACT)
    echo "### $mode ($([ "$mode" = cold ] && echo 'Cache-Control: no-cache, every request hits Postgres' || echo 'cache enabled'))"
    uv run --with httpx python tools/lb_bench.py --email "$EMAIL" --password "$PASS" \
      --path "/memories/backups?limit=200" --duration "${DUR:-20}" --concurrency "${CONC:-30}" $flag
    after=$(DB_XACT); echo "postgres rows read during run (tup_returned+tup_fetched): $((after-before))"
  done ;;
esac
