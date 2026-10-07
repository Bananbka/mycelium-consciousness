#### baseline (50 VUsers, 3 min)

| Metric | 0 · before | 1 · query/IO/argon2 fixes | 2 · + Redis cache | Δ first→last |
| --- | --- | --- | --- | --- |
| RPS (avg) | 72 | 73 | 73 | +1% |
| RPS (peak) | 80 | 81 | 81 | +1% |
| Latency avg, ms | 34 | 33 | 31 | -9% |
| Latency p50, ms | 48 | 48 | 46 | -4% |
| Latency p95, ms | 57 | 55 | 52 | -9% |
| Latency p99, ms | 68 | 61 | 55 | -19% |
| Error rate, % | 0.00 | 0.00 | 0.00 | n/a |
| api CPU avg, % | 43 | 39 | 28 | -34% |
| api RAM peak, MiB | 301 | 304 | 302 | +0% |

Per endpoint, average / p95 ms:

| Endpoint | 0 · before | 1 · query/IO/argon2 fixes | 2 · + Redis cache |
| --- | --- | --- | --- |
| GET /health | 6 / 10 | 6 / 9 | 5 / 8 |
| GET /memories/backups | 9 / 14 | 8 / 12 | 5 / 8 |
| GET /memories/stream/status | 8 / 13 | 7 / 11 | 5 / 7 |
| GET /profiles/me | 8 / 12 | 6 / 10 | 4 / 6 |
| POST /auth/login | 87 / 120 | 92 / 120 | 85 / 100 |
| POST /memories/write | 52 / 58 | 51 / 56 | 48 / 53 |

#### step (50 → 600 VUsers, 9 min)

| Metric | 0 · before | 1 · query/IO/argon2 fixes | 2 · + Redis cache | Δ first→last |
| --- | --- | --- | --- | --- |
| Requests | 146685 | 167629 | 232678 | +59% |
| RPS (avg) | 271 | 310 | 431 | +59% |
| RPS (peak) | 380 | 429 | 698 | +84% |
| Latency avg, ms | 587 | 439 | 141 | -76% |
| Latency p50, ms | 130 | 130 | 58 | -55% |
| Latency p95, ms | 2700 | 1800 | 520 | -81% |
| Error rate, % | 0.00 | 0.00 | 0.00 | -100% |
| api CPU avg, % | 182 | 204 | 174 | -5% |
| api RAM peak, MiB | 382 | 515 | 453 | +19% |

By load step (first third of each step discarded):

| Users | RPS 0 · before | p95 ms | err % | RPS 1 · query/IO/argon2 fixes | p95 ms | err % | RPS 2 · + Redis cache | p95 ms | err % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 78 | 300 | 0.0 | 79 | 81 | 0.0 | 79 | 57 | 0.0 |
| 100 | 151 | 304 | 0.0 | 156 | 95 | 0.0 | 157 | 55 | 0.0 |
| 150 | 216 | 392 | 0.0 | 233 | 168 | 0.0 | 236 | 56 | 0.0 |
| 200 | 273 | 431 | 0.0 | 300 | 244 | 0.0 | 314 | 60 | 0.0 |
| 250 | 316 | 512 | 0.0 | 344 | 332 | 0.0 | 390 | 66 | 0.0 |
| 300 | 310 | 740 | 0.0 | 354 | 474 | 0.0 | 467 | 89 | 0.0 |
| 350 | 316 | 1066 | 0.0 | 365 | 695 | 0.0 | 537 | 134 | 0.0 |
| 400 | 337 | 1400 | 0.0 | 401 | 878 | 0.0 | 595 | 199 | 0.0 |
| 450 | 344 | 1660 | 0.0 | 407 | 1028 | 0.0 | 639 | 255 | 0.0 |
| 500 | 340 | 2017 | 0.0 | 408 | 1237 | 0.0 | 657 | 330 | 0.0 |
| 550 | 352 | 2320 | 0.0 | 402 | 1434 | 0.0 | 643 | 419 | 0.0 |
| 600 | 350 | 2650 | 0.0 | 393 | 1667 | 0.0 | 634 | 497 | 0.0 |
