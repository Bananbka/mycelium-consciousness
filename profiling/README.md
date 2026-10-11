# Lab 9 — performance profiling and bottleneck diagnostics

Cycle: **symptom → hypotheses → instrumentation → evidence → root cause →
optimisation → regression check**. Everything below was measured on the same
laptop and compose topology as lab 5 (2 `api` replicas × 2 workers, k6 limited
to 2 CPUs, 10 s warm-up + 20 s measured per point; one run per point, so
differences under ~10 % are noise). The stack is the lab 8 build (replica and
two shards on).

## 1. Symptom

From the lab 5 baseline, still true on the lab 8 build: throughput is flat at
~250–300 RPS from ~10 VUs up, the two `api` containers sit at ~200 % CPU each
(≈14.5 ms of API CPU per write request), Postgres at ~43 %, and the tail grows
with VUs. The system is **API-CPU-bound**, but nobody had looked at *what* in
a request burns the CPU.

## 2. Hypotheses

| | Hypothesis | Prediction if true |
| --- | --- | --- |
| H1 | JWT verification / Pydantic / JSON serialisation dominate | those frames top the CPU profile |
| H2 | A slow or unindexed SQL statement dominates | `EXPLAIN ANALYZE` shows Seq Scan / ms-level execution; DB CPU high |
| H3 | Framework + middleware (two `BaseHTTPMiddleware`) + Redis dominate | an endpoint with no auth and no I/O is nearly as slow as the real one |
| H4 | Per-request database *round trips* dominate (ORM + driver + pool) | an endpoint that only checks out a connection and runs `SELECT 1` already loses a large share of throughput; the statement log shows more statements than needed |

## 3. Instrumentation

| Tool | What it answers | Artifact |
| --- | --- | --- |
| `py-spy record --subprocesses` (sampling, 250 Hz, sidecar container sharing the api PID namespace) | where CPU time goes in the workers | `artifacts/b_baseline.svg` / `.txt.gz`, `artifacts/B_opt.svg` / `.txt.gz` |
| `summarize_stacks.py` | per-library share of samples | tables below |
| layer-isolation probes `/_prof/{noop,redis,db,auth}` (`PROFILING_ENDPOINTS=1`) + k6 scenario `P` | cost of each layer as a throughput difference | `load-tests/results/l9_layer_*.json`, `artifacts/layer_before_*.json` |
| `log_statement=all` for one request | what Postgres actually receives | `artifacts/pg_statement_trace_auth.txt` |
| `EXPLAIN (ANALYZE, BUFFERS)` | cost of the statements themselves | `artifacts/explain_auth.txt`, `artifacts/explain_backups.txt` |
| `docker stats` during runs | CPU / memory per container | `load-tests/results/l9_*.stats` |

Full distributed tracing (OpenTelemetry/Jaeger) was **not** set up: the
request crosses nginx → api → {Postgres, Redis} and every hop is observable
with the tools above; the layer ladder gives the same decomposition without an
exporter. That is a stated limit, not a substitute for production tracing.

## 4. Evidence

**CPU profile (baseline, scenario B, 50 VUs, share of worker samples):**

| Bucket (inclusive) | Share |
| --- | --- |
| SQLAlchemy ORM / Core | **36.6 %** |
| Redis client | 8.0 % |
| asyncpg driver | 5.3 % |
| JWT / crypto | 1.6 % |
| Pydantic / serialisation | 1.3 % |

By leaf library: asyncio 30 %, anyio 14 % (task groups of
`BaseHTTPMiddleware`), SQLAlchemy 8.8 %, Redis 6.6 %, asyncpg 5.2 %, JWT 1.4 %.

**Layer ladder (50 VUs, RPS / p95 ms):**

| Probe | Adds | Before | After |
| --- | --- | --- | --- |
| `/_prof/noop` | framework + 2 middlewares | 1323 / 29 | 1517 / 24 |
| `/_prof/redis` | + 1 Redis round trip | 1296 / 29 | 1235 / 31 |
| `/_prof/db` | + pooled checkout, `SELECT 1` | **648 / 65** | 804 / 45 |
| `/_prof/auth` | + JWT + user/profile lookup | **389 / 102** | 570 / 75 |
| `POST /memories/write` | + validation, `XADD`+`XLEN`, write marker | 292 / 150 | 389 / 105 |

**What Postgres receives for one authenticated request**
(`artifacts/pg_statement_trace_auth.txt`):

```
BEGIN; ROLLBACK;                                        <- pool_pre_ping probe on checkout
BEGIN;
SELECT ... FROM users WHERE id = $1
SELECT ... FROM clone_profiles WHERE user_id IN ($1)    <- selectinload, a 2nd round trip
ROLLBACK;                                               <- session close
```

Six statements, two of them useful.

**Statement cost** (`explain_auth.txt`, `explain_backups.txt`): user by primary
key 0.04 ms, profile by unique `user_id` 0.04 ms, newest-50 `memory_backups`
via `ix_memory_backups_clone_id_period_end` 0.27 ms; `SELECT 1` 0.006 ms. The
statements are cheap; the *number* of trips is not.

### Verdicts

| | Verdict | Why |
| --- | --- | --- |
| H1 | refuted | JWT 1.6 %, Pydantic 1.3 % of samples |
| H2 | refuted | all statements < 0.3 ms with index scans; Postgres has headroom (43 %). The drop-index control is a seq scan over only ~1.3k rows (0.15 ms) and says nothing about production size |
| H3 | refuted as the main cause | the no-I/O probe does 1300+ RPS through the same middleware; one Redis trip costs ~0 RPS. Middleware overhead is real (anyio 14 % leaf) but is not what separates 1300 from 290 |
| H4 | **confirmed** | one checkout + `SELECT 1` halves throughput (1296 → 648); auth costs another 40 %; 3 of the 5 round trips per request are overhead |

## 5. Root cause

*Symptom:* API CPU saturates at ~250–300 RPS. *Cause:* every request pays
**five database round trips for two useful statements**; each trip is a
driver call, a greenlet switch and an event-loop hop in API CPU (and a wakeup
in Postgres). The extras came from configuration, not business logic:

1. `pool_pre_ping=True` — a `BEGIN; ROLLBACK;` probe on *every* checkout.
2. `selectinload(User.profile)` in the auth dependency — a second statement
   for a strictly one-to-one row.

## 6. Optimisation

| Change | Where |
| --- | --- |
| `joinedload` instead of `selectinload`: user + profile in one `LEFT JOIN` | `services/api/src/api/deps.py` |
| `pool_pre_ping` off by default (`DB_POOL_PRE_PING=1` restores it); applied to primary, replica and shard engines | `shared/db/db.py`, `shared/sharding.py` |

## 7. A/B result (same hardware, topology, VUs)

Three builds: **base** (pre-ping on, `selectinload`), **join** (pre-ping on,
`joinedload`), **opt** (both changes). Cache bypassed (`NOCACHE=1`), 0 %
errors in every run. RPS, p95 and p99 in ms, API CPU is the sum over the two
api containers (400 % = 4 saturated cores).

| Scenario | VUs | base RPS | join RPS | opt RPS | Δ RPS | base p95 | join p95 | opt p95 | Δ p95 | base p99 | opt p99 | API CPU base → opt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A read | 10 | 238 | 247 | 315 | +32% | 35 | 38 | 24 | −29% | 57 | 39 | 437% → 434% |
| A read | 50 | 200 | 232 | 260 | +30% | 238 | 194 | 185 | −22% | 344 | 285 | 424% → 446% |
| A read | 100 | 198 | 214 | 261 | +31% | 513 | 496 | 415 | −19% | 705 | 591 | 429% → 439% |
| B write | 10 | 326 | 337 | 411 | +26% | 25 | 27 | 23 | −11% | 37 | 30 | 412% → 463% |
| B write | 50 | 290 | 347 | 391 | +35% | 150 | 109 | 98 | −35% | 203 | 167 | 442% → 434% |
| B write | 100 | 283 | 331 | 384 | +36% | 283 | 244 | 214 | −24% | 364 | 268 | 409% → 416% |
| C workflow | 10 | 305 | 359 | 409 | +34% | 31 | 26 | 23 | −23% | 44 | 30 | 487% → 395% |
| C workflow | 50 | 291 | 349 | 390 | +34% | 158 | 124 | 112 | −29% | 209 | 175 | 414% → 438% |
| C workflow | 100 | 283 | 339 | 384 | +36% | 336 | 265 | 259 | −23% | 463 | 356 | 418% → 436% |

(`artifacts/ab_table.md` has the full table, `python profiling/compare.py base join opt`.)

Write path: **+26…36 % RPS, p95 −11…−35 %**; read path +30 % RPS, p95
−19…−29 %; mixed workflow +34…36 %. API CPU stays at ~200 % per container —
the workers are still saturated, they simply do less per request (≈14.5 ms →
≈10.8 ms of API CPU per write at 100 VUs; Postgres CPU 43 % → 35 %). Memory is
unchanged (api ≈ 238 MiB before and after, no growth in any run, so no leak
was found; this is RSS sampling only — no heap dump was taken).

Attribution: the single JOIN alone gives +3…20 % (`join` column); removing
pre-ping adds the rest.

## 8. Trade-offs and regression risks

| Change | Cost | Mitigation / check |
| --- | --- | --- |
| pre-ping off | a connection the server silently dropped is discovered on first use instead of at checkout, so one request can fail | `pool_recycle=1800`; the lab 6 handler turns connectivity errors into 503 (reads fall back to the standby); control `artifacts/preping_off_failure.txt`: after `pg_terminate_backend` of all 41 backends, 12/12 requests were still answered. Not a guarantee for a request caught mid-query |
| `joinedload` | the profile row is read even where only `users` is needed (admin routes: LEFT JOIN against nothing), the row is wider | identical semantics, 195 tests pass; the join executes in 0.19 ms vs 0.04 + 0.04 ms for two statements — the saving is round trips, not execution time |
| `/_prof/*` probes | an unauthenticated no-op endpoint | mounted only with `PROFILING_ENDPOINTS=1`, hidden from the schema, default `0` |
| py-spy sidecar | sampling overhead (a few %), `SYS_PTRACE` on a throw-away container | api image untouched |

## 9. What the profile still shows (not fixed here)

- `BEGIN`/`ROLLBACK` per read request (AUTOCOMMIT for the read-only auth
  lookup collides with handlers that write on the same session).
- `XADD` + `XLEN` as two Redis trips (a pipeline would halve it).
- Two stacked `BaseHTTPMiddleware` (anyio ≈14 % of leaf samples); pure-ASGI
  middleware would remove a task group per request.
- Python workers remain the ceiling: `/_prof/noop` is ~3× faster than
  authenticated reads, so the next tier is more cores/processes, not tuning.

## 10. Reproduce

```bash
docker build -t lab9-pyspy -f profiling/Dockerfile.pyspy profiling
PROFILING_ENDPOINTS=1 docker compose -f infra/docker-compose.yaml up -d --build
uv run --with httpx python profiling/seed_lt.py        # backups for the lt-* accounts (needs ADMIN_EMAIL/PASSWORD)
bash profiling/run.sh layers                           # layer ladder
bash profiling/run.sh spy B baseline                   # flame graph under scenario B
bash profiling/run.sh ab opt                           # A/B matrix (DB_POOL_PRE_PING=1 for the pre-ping-on builds)
python profiling/summarize_stacks.py profiling/artifacts/b_baseline.txt.gz
python profiling/compare.py base join opt
```

## 11. Theory notes

- **Symptom vs root cause.** "CPU is saturated" and "p99 grows" are symptoms;
  "five round trips for two statements because of pre-ping and a second eager
  load" is the cause. A fix aimed at the symptom (more replicas) bought only
  sub-linear scaling in lab 5.
- **Sampling vs instrumentation profilers.** A sampling profiler (py-spy)
  reads the stack at a fixed rate from outside the process: tiny overhead, no
  code change, statistical (short rare calls can be missed), and it loses
  caller attribution across greenlets/awaits. An instrumentation profiler
  (cProfile, tracing spans) hooks every call/span: exact counts and call
  trees, but overhead that distorts what it measures.
- **Why differencing.** SQLAlchemy runs its sync core inside greenlets, so
  py-spy's caller attribution breaks at the `await`; the layer ladder measures
  the same thing from outside.
