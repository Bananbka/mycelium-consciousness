# Lab 6 — Load balancing with nginx

Environment: one Windows 11 / Docker Desktop laptop; Postgres, Redis, MinIO,
Celery, the 3 `api` replicas (`API_WORKERS=2` each), nginx and the load
generator all share its CPU, so absolute numbers are comparable only with each
other. Everything below was produced by `scripts/lb_experiments.sh` (raw output
in the gitignored `results/`).

## 1. Replicas and instance identification

`infra/docker-compose.yaml` runs `api` with `deploy.replicas: ${API_REPLICAS:-3}`
(or `--scale api=N`). Replicas publish no host port, so the pool is reachable
only through nginx. Docker's DNS returns one A record per replica for `api`.

Every response carries `X-Instance-ID` (container hostname, overridable by
`INSTANCE_ID`); `GET /health` returns it in the body as `instance`. nginx adds
`X-Upstream-Addr` with the IP of the replica it picked, which cross-checks the
API's own claim. Covered by `tests/test_instance_id.py`.

## 2. nginx as load balancer

`infra/nginx/default.conf.template` (rendered by the image's `envsubst` into
`conf.d/default.conf`):

```nginx
upstream api_pool {
    ${LB_ALGORITHM}          # empty = round robin | least_conn; | ip_hash;
    ${LB_SERVERS}            # server api:8000 max_fails=2 fail_timeout=10s;
    zone api_pool 64k;       # state shared by all nginx workers
    keepalive 32;
}
server { listen 80; location / { proxy_pass http://api_pool; ... } }
```

`server api:8000` expands to the IP of every replica when nginx starts, so run
`docker compose -f infra/docker-compose.yaml restart nginx` after scaling.
`zone` matters: without it each nginx worker process keeps its own round-robin
counter and the rotation visibly stutters (observed: `A B C A B C A A B C`).

Switch algorithm live:
`LB_ALGORITHM="least_conn;" docker compose -f infra/docker-compose.yaml up -d --no-deps --force-recreate nginx`.

## 3. Round robin demonstration

`scripts/lb_experiments.sh distribution` — 20 sequential `GET /health` through
`http://localhost`, 3 replicas:

```
 1  de1d51d6460d 172.22.0.2:8000      11  fd4cfbc74cc4 172.22.0.4:8000
 2  fd4cfbc74cc4 172.22.0.4:8000      12  d6ef2ddb5184 172.22.0.3:8000
 3  d6ef2ddb5184 172.22.0.3:8000      13  de1d51d6460d 172.22.0.2:8000
 4  de1d51d6460d 172.22.0.2:8000      14  fd4cfbc74cc4 172.22.0.4:8000
 5  fd4cfbc74cc4 172.22.0.4:8000      15  d6ef2ddb5184 172.22.0.3:8000
 6  d6ef2ddb5184 172.22.0.3:8000      16  de1d51d6460d 172.22.0.2:8000
 7  de1d51d6460d 172.22.0.2:8000      17  fd4cfbc74cc4 172.22.0.4:8000
 8  fd4cfbc74cc4 172.22.0.4:8000      18  d6ef2ddb5184 172.22.0.3:8000
 9  d6ef2ddb5184 172.22.0.3:8000      19  de1d51d6460d 172.22.0.2:8000
10  de1d51d6460d 172.22.0.2:8000      20  fd4cfbc74cc4 172.22.0.4:8000
```

Strict rotation over three replicas: 7 / 7 / 6 requests.

## 4. Fault tolerance (health checks)

Open-source nginx has **passive** health checking only: a replica that errors
or times out `max_fails` (2) times within `fail_timeout` (10 s) is taken out of
rotation for `fail_timeout`, and `proxy_next_upstream error timeout http_502
http_503 http_504 non_idempotent` retries the failed request on another replica
(`non_idempotent` lets POST be retried too — a write frame that died with its
node can be duplicated, which append-only memory tolerates; a non-idempotent
business operation would need an idempotency key). Active probes need nginx
Plus, HAProxy or Traefik. Docker's own `/health` healthcheck on each replica
keeps `docker compose` aware of a sick container, but it does not feed nginx.

`scripts/lb_experiments.sh failover` (`docker stop clone-sandbox-api-2`):

| Phase | HTTP codes | Replicas answering (12 requests) |
| --- | --- | --- |
| before | – | 3 / 3 / 3 (9 requests) |
| replica 2 stopped | 12 × 200 | 6 / 6 on the two survivors, 0 on the stopped one |
| replica 2 started again, 15 s later | – | 4 / 4 / 4 — back in the pool, no nginx restart |

Under load (`failover-load`: 20 workers POSTing `/memories/write` for 25 s,
replica stopped at t = 8 s): **1898 requests, 0 errors**; the stopped replica
served 99 requests before the stop, the other two ~900 each. With nginx's
default retry (idempotent methods only) the same experiment on the earlier
`alt-3` branch (measured there, not re-run here) gave 0.33 % 502s, which is what `non_idempotent` removes.

## 5. Alternative algorithms (additional task 1)

One fast replica plus `api-slow` (same image, `SIMULATED_LATENCY_MS=300`,
compose profile `slow`); 30 closed-loop workers, 20 s,
`scripts/lb_experiments.sh algorithms` (`GET /profiles/me`):

| Algorithm | RPS | p50 ms | p95 ms | p99 ms | Slow / fast share |
| --- | --- | --- | --- | --- | --- |
| round robin | 75.0 | 355 | 1159 | 1884 | 50 % / 50 % |
| **least_conn** | **104.1** | **193** | **815** | **1316** | 6 % / 94 % |
| weighted RR (fast 3 : slow 1) | 85.4 | 273 | 983 | 1546 | 25 % / 75 % |
| ip_hash | 51.1 | 364 | 1631 | 3790 | 100 % / 0 % (single client IP) |

- `least_conn` adapts to the real state: the slow replica holds each connection
  300 ms longer, so it always looks busy and gets only 6 % of requests —
  better than a static 3 : 1 weight, which needs the operator to know the ratio.
- Weighted RR delivers exactly the configured 25 / 75 split but is blind to load.
- Round robin is worst here: half the traffic is paid for with 300 ms.
- `ip_hash` pins every request from one client address to one replica; with a
  single load generator that is the degenerate case (everything hit one node).
  It is only worth it for session affinity, which the stateless API (JWT in
  Postgres/Redis-backed state) does not need.

## 6. One replica vs. three (additional task 2)

Lab 4's Locust scenario (profile `lb` in `loadtests/profiles.py`: 100 users, 10 s
ramp, 60 s steady, 0.2–1 s think time, mixed read/write), round robin through
nginx, `scripts/lb_experiments.sh compare`:

| Replicas | Requests | RPS | Avg ms | p50 ms | p95 ms | p99 ms | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 8670 | 122.8 | 164 | 56 | 620 | 2400 | 0 |
| 3 | 9400 | 133.4 | 115 | 55 | **100** | 2700 | 0 |

Per endpoint, `POST /memories/write`: avg 173 → 115 ms, p95 600 → 100 ms.
RPS barely moves because Locust is closed-loop with think time: 100 users can
offer at most ~140 req/s, and both configurations nearly satisfy that. The gain
shows up as **latency** — the 95th percentile drops six-fold, i.e. requests
stop queueing behind each other inside one process pool.

Saturating closed-loop benchmark without think time (`scaleout`,
`tools/lb_bench.py`, 30 workers, 20 s):

| Replicas | Write RPS | Write p99 ms | Read RPS | Read p99 ms |
| --- | --- | --- | --- | --- |
| 1 | 79.7 | 2461 | 105.3 | 1303 |
| 2 | 78.1 | 2586 | 102.2 | 1329 |
| 3 | 75.5 | 2662 | 99.1 | 1401 |

**No scale-out at all** — throughput is flat and p99 slightly worsens. The API
tier is not the bottleneck: every authenticated request does a Postgres round
trip, and Postgres, Redis, MinIO, the Docker VM and the load generator share one
machine's CPU, so extra replicas only add contention for the same resources
(see `docs/bottleneck-analysis.md`). Horizontal scaling of a stateless tier
pays off when that tier is the limit; here the next step would be Postgres
(connection pool sizing, read replicas) and a separate host for the load
generator. Distribution stays perfectly even (33.3 % each) in every run.

## 7. The balancer itself

A single nginx container is still a single point of failure. Remedies: two
nginx nodes behind a virtual IP (Keepalived/VRRP), DNS round robin across
several balancers, or a cloud L4 balancer in front of an nginx pair.
