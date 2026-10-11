#!/usr/bin/env bash
# Lab 9 runner.
#   profiling/run.sh layers          # RPS ladder: noop -> redis -> db -> auth -> write (cost per layer)
#   profiling/run.sh spy B <tag>     # scenario B under load + py-spy raw/flamegraph into profiling/artifacts
#   profiling/run.sh ab <tag>        # scenarios A/B/C at 10/50/100 VUs -> load-tests/results/l9_<tag>_*.json
#   profiling/run.sh matrix <tag>    # lab 10: lab 5 profile (cache on) A/B/C x 10/50/100/200 VUs -> l10_<tag>_*
#   profiling/run.sh explain         # EXPLAIN (ANALYZE, BUFFERS) of the hot statements
# COMPOSE, NETWORK, ENV_FILE, API (container to attach py-spy to) as in run_load_tests.sh.
export MSYS_NO_PATHCONV=1
set -euo pipefail
cd "$(dirname "$0")/.."
C="${COMPOSE:-docker compose -f infra/docker-compose.yaml}"
NET="${NETWORK:-infra_default}"
set -a; . <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "${ENV_FILE:-infra/.env}"); set +a
DURATION="${DURATION:-20s}"
VUS="${VUS:-50}"
API="${API:-$($C ps -q api | head -1)}"
OUT=profiling/artifacts
mkdir -p "$OUT"

k6run() { # name scenario vus [extra -e args]
  local name="$1" scen="$2" vus="$3"; shift 3
  ( sleep $(( ${DURATION%s} * 2 / 3 + 10 )); docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' > "load-tests/results/$name.stats" 2>/dev/null ) &
  docker run --rm -i --cpus=2 --network "$NET" -v "$PWD/load-tests:/lt" grafana/k6:latest run --quiet \
    -e SCENARIO="$scen" -e VUS="$vus" -e DURATION="$DURATION" -e BASE_URL=http://nginx "$@" \
    --summary-export="/lt/results/$name.json" /lt/mycelium.js >/dev/null 2>"load-tests/results/$name.err" || true
  wait
  python load-tests/scrub.py "load-tests/results/$name.json"
}

spy() { # file-stem seconds-after-start
  local stem="$1" delay="$2" pid
  sleep "$delay"
  pid=$(docker run --rm --pid=container:"$API" lab9-pyspy sh -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline | grep -q "bin/ap[i]" && echo $(basename $p) && break; done')
  for fmt in raw flamegraph; do
    ext=$([ $fmt = raw ] && echo txt || echo svg)
    docker run --rm --pid=container:"$API" --cap-add SYS_PTRACE -v "$PWD/$OUT:/out" lab9-pyspy \
      py-spy record -d 10 -r 250 --subprocesses --pid "$pid" -f $fmt -o /out/$stem.$ext >/dev/null 2>&1 || true
  done
  gzip -9 -f "$OUT/$stem.txt"   # raw stacks are several MB
}

case "${1:?layers|spy|ab|explain}" in
layers)
  for probe in noop redis db auth; do
    k6run "l9_layer_${probe}" P "$VUS" -e PATH_UNDER_TEST="/_prof/${probe}"
  done
  k6run "l9_layer_write" B "$VUS" ;;
spy)
  tag="${3:-baseline}"
  spy "${2:-B}_$tag" 14 &
  k6run "l9_spy_${2:-B}_$tag" "${2:-B}" "$VUS" -e WARMUP=10s
  wait ;;
ab)
  tag="${2:?tag}"
  for scen in A B C; do for v in 10 50 100; do
    k6run "l9_${tag}_${scen}_${v}" "$scen" "$v" -e NOCACHE=1
  done; done ;;
matrix)   # lab 10: the lab 5 load profile (cache ON), A/B/C x VUs, 30 s each
  tag="${2:?tag}"
  for scen in A B C; do for v in ${LEVELS:-10 50 100 200}; do
    DURATION="${DURATION:-30s}" k6run "l10_${tag}_${scen}_${v}" "$scen" "$v"
  done; done ;;
one)      # lab 10: one run, any scenario: run.sh one <name> <scenario> <vus>
  DURATION="${DURATION:-30s}" k6run "l10_${2:?name}" "${3:?scenario}" "${4:?vus}" ;;
explain)
  $C exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f - < profiling/explain.sql ;;
esac
