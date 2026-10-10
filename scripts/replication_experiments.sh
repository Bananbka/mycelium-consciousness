#!/usr/bin/env bash
# Lab 6 experiments against the running stack (primary + streaming standby).
#   scripts/replication_experiments.sh topology   # who is primary/replica, standby is read-only
#   scripts/replication_experiments.sh routing    # write -> sticky window -> replica; strong/eventual override
#   scripts/replication_experiments.sh lag [3s]   # inject replay delay: stale read vs read-after-write
#   scripts/replication_experiments.sh failover   # stop replica, then stop primary
#
# COMPOSE overrides the compose invocation (e.g. a second project on other ports),
# ENV_FILE the env file used for the database credentials.
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
BASE="${BASE:-http://localhost}"
JSON='Content-Type: application/json'
CR=$(printf '\r')
# Strip a UTF-8 BOM and CRs a Windows editor may have left in the env file.
set -a; . <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${ENV_FILE:-infra/.env}"); set +a

psql_()  { $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
rpsql_() { $C exec -T db-replica psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"; }
pyjson() { python -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

EMAIL="repl-$(date +%s)@example.com"
PASS="$(python -c 'import uuid;print(uuid.uuid4().hex)')"
curl -s -X POST "$BASE/auth/register" -H "$JSON" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\",\"designation\":\"r-$$\"}" >/dev/null
TOKEN=$(curl -s -X POST "$BASE/auth/login" -H "$JSON" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | pyjson 'd["access_token"]')
AUTH="Authorization: Bearer $TOKEN"
PID=$(curl -s "$BASE/profiles/me" -H "$AUTH" | pyjson 'd["id"]')

# req METHOD PATH [curl args...] -> prints "HTTP <code>  node=<X-DB-Node>"; body is left in $BODY
BODY=""
req() {
  local method="$1" path="$2"; shift 2
  local raw code node
  raw=$(curl -s -i --max-time 15 -X "$method" "$BASE$path" -H "$AUTH" "$@" || true)
  raw=${raw//$CR/}
  code=$(printf '%s\n' "$raw" | awk 'NR==1{print $2}')
  node=$(printf '%s\n' "$raw" | awk -F': ' 'tolower($1)=="x-db-node"{print $2; exit}')
  BODY=$(printf '%s\n' "$raw" | sed '1,/^$/d')
  printf 'HTTP %s  node=%-17s' "${code:-000}" "${node:--}"
}
status_of() { printf '%s' "$BODY" | pyjson 'd["status"]' 2>/dev/null || echo "?"; }
read_profile() { # [consistency]
  req GET "/profiles/$PID" ${1:+-H "X-Read-Consistency: $1"}; echo " status=$(status_of)"
}
write_status() { # value
  req PATCH "/profiles/me" -H "$JSON" -d "{\"status\":\"$1\"}"; echo " wrote status=$1"
}

case "${1:-routing}" in
topology)
  echo "== primary: streaming standbys (pg_stat_replication)"
  $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "SELECT application_name, state, sync_state, pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes FROM pg_stat_replication"
  echo "== roles"
  echo "primary  in_recovery=$(psql_ 'SELECT pg_is_in_recovery()')  wal_level=$(psql_ 'SHOW wal_level')"
  echo "replica  in_recovery=$(rpsql_ 'SELECT pg_is_in_recovery()')"
  echo "== a write on the standby is refused"
  $C exec -T db-replica psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE TABLE _w (i int)" 2>&1 | tail -2 || true
  echo "== data written on the primary shows up on the standby"
  echo "primary users: $(psql_ 'SELECT count(*) FROM users')"
  sleep 1
  echo "replica users: $(rpsql_ 'SELECT count(*) FROM users')" ;;

routing)
  echo "== 1. no recent write: lag-tolerant read goes to the replica"
  read_profile
  echo "== 2. write, then read straight away: read-after-write window pins it to the primary"
  write_status routed-1
  read_profile
  echo "== 3. after the window (REPLICA_STICKY_SECONDS=${REPLICA_STICKY_SECONDS:-5}s) the replica serves reads again"
  sleep $(( ${REPLICA_STICKY_SECONDS:-5} + 1 ))
  read_profile
  echo "== 4. explicit override per request (X-Read-Consistency)"
  read_profile strong
  read_profile eventual
  echo "== 5. strongly consistent paths never use the replica (auth, /profiles/me)"
  req GET /profiles/me; echo ;;

lag)
  DELAY="${2:-3s}"
  reset() { $C exec -T db-replica psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -c "ALTER SYSTEM RESET recovery_min_apply_delay" -c "SELECT pg_reload_conf()" >/dev/null; }
  trap reset EXIT
  echo "== standby replays WAL $DELAY late (recovery_min_apply_delay)"
  $C exec -T db-replica psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "ALTER SYSTEM SET recovery_min_apply_delay = '$DELAY'" -c "SELECT pg_reload_conf()" >/dev/null
  sleep 2
  write_status before-lag
  sleep $(( ${DELAY%s} + 1 ))
  write_status fresh-write
  echo "-- immediately, forced to the replica (what a naive router would do):"
  read_profile eventual
  echo "-- immediately, automatic routing (read-after-write):"
  read_profile
  echo "-- WAL received by the standby but not yet replayed (bytes):"
  rpsql_ "SELECT pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn())"
  echo "-- after $DELAY the standby has caught up:"
  sleep $(( ${DELAY%s} + 1 ))
  read_profile eventual ;;

failover)
  wait_replica() { for _ in $(seq 1 40); do
      [ "$(rpsql_ 'SELECT pg_is_in_recovery()' 2>/dev/null || true)" = t ] && return 0; sleep 1; done; return 1; }
  health() { curl -s -w '  HTTP %{http_code}\n' --max-time 15 "$BASE/health"; }
  echo "== baseline"; read_profile; echo -n "/health "; health

  echo "== A. replica down: reads fall back to the primary, nothing fails"
  $C stop db-replica >/dev/null; sleep 1
  read_profile; read_profile
  echo -n "/health "; health
  echo "== replica back: the breaker closes after its cooldown, reads return to the replica"
  $C start db-replica >/dev/null; wait_replica; sleep $(( ${REPLICA_BREAKER_COOLDOWN_SECONDS:-5} + 1 ))
  read_profile; read_profile

  echo "== B. primary down: reads are served from the standby, writes are refused"
  $C stop db >/dev/null; sleep 1
  echo -n "read      "; read_profile
  echo -n "list      "; req GET /profiles; echo
  echo -n "write     "; write_status during-outage
  echo -n "backup id "; req GET /memories/backups/1; echo "  (primary-only path)"
  echo -n "/health   "; health
  echo "== primary back"
  $C start db >/dev/null
  for _ in $(seq 1 40); do [ "$(psql_ 'SELECT 1' 2>/dev/null || true)" = 1 ] && break; sleep 1; done
  sleep 3
  write_status after-recovery
  read_profile
  echo -n "/health "; health ;;

*) echo "usage: $0 topology|routing|lag [delay]|failover" >&2; exit 2 ;;
esac
