### baseline

| Metric | Value |
| --- | --- |
| Requests | 13015 |
| RPS (average / peak) | 72.2 / 80.0 |
| Latency avg / min / max | 37 / 3 / 357 ms |
| Latency p50 / p95 / p99 | 50 / 63 / 92 ms |
| Error rate | 0.00% (0) |

| Endpoint | Requests | Avg ms | p95 ms | p99 ms | Max ms | Errors |
| --- | --- | --- | --- | --- | --- | --- |
| GET /health | 739 | 7 | 12 | 25 | 121 | 0 |
| GET /memories/backups | 1472 | 12 | 23 | 39 | 246 | 0 |
| GET /memories/stream/status | 785 | 11 | 22 | 45 | 153 | 0 |
| GET /profiles/me | 2304 | 10 | 18 | 31 | 297 | 0 |
| POST /auth/login | 50 | 180 | 340 | 360 | 357 | 0 |
| POST /memories/write | 7665 | 55 | 67 | 90 | 296 | 0 |

| Service | CPU avg % | CPU peak % | RAM peak MiB |
| --- | --- | --- | --- |
| api | 49 | 174 | 307 |
| celery-beat | 0 | 1 | 82 |
| celery-worker | 1 | 40 | 790 |
| db | 9 | 24 | 86 |
| minio | 2 | 15 | 106 |
| nginx | 3 | 5 | 4 |
| redis | 3 | 7 | 11 |
