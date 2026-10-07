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

| Users | RPS 0 · before | avg ms | err % | RPS 1 · query/IO/argon2 fixes | avg ms | err % | RPS 2 · + Redis cache | avg ms | err % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 78 | 33 | 0.0 | 79 | 33 | 0.0 | 79 | 31 | 0.0 |
| 100 | 151 | 51 | 0.0 | 156 | 34 | 0.0 | 157 | 31 | 0.0 |
| 150 | 216 | 77 | 0.0 | 233 | 35 | 0.0 | 236 | 32 | 0.0 |
| 200 | 273 | 116 | 0.0 | 300 | 68 | 0.0 | 314 | 33 | 0.0 |
| 250 | 316 | 192 | 0.0 | 344 | 129 | 0.0 | 390 | 35 | 0.0 |
| 300 | 310 | 336 | 0.0 | 354 | 238 | 0.0 | 467 | 36 | 0.0 |
| 350 | 316 | 477 | 0.0 | 365 | 341 | 0.0 | 537 | 40 | 0.0 |
| 400 | 337 | 571 | 0.0 | 401 | 380 | 0.0 | 595 | 64 | 0.0 |
| 450 | 344 | 690 | 0.0 | 407 | 494 | 0.0 | 639 | 98 | 0.0 |
| 500 | 340 | 840 | 0.0 | 408 | 613 | 0.0 | 657 | 143 | 0.0 |
| 550 | 352 | 900 | 0.0 | 402 | 726 | 0.0 | 643 | 228 | 0.0 |
| 600 | 350 | 1060 | 0.0 | 393 | 910 | 0.0 | 634 | 321 | 0.0 |
