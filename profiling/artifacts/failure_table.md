| Failure | Scenario | Requests | Failed | Error % | p50 ms | p95 ms | p99 ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| one api instance stopped | A read | 21715 | 0 | 0.0 | 83 | 185 | 271 |
| one api instance stopped | B write | 24438 | 0 | 0.0 | 62 | 185 | 290 |
| Redis stopped | A read | 16063 | 1 | 0.0 | 64 | 515 | 539 |
| Redis stopped | B write | 20609 | 1046 | 5.1 | 52 | 176 | 1010 |
| Redis stopped | C workflow | 23496 | 1029 | 4.4 | 52 | 130 | 1006 |
| read replica stopped | A read | 28558 | 380 | 1.3 | 62 | 133 | 200 |
| read replica stopped | B write | 24734 | 288 | 1.2 | 73 | 140 | 209 |
| shard-b stopped | A read | 30596 | 0 | 0.0 | 60 | 101 | 154 |
| shard-b stopped | B write | 36080 | 0 | 0.0 | 52 | 79 | 137 |
| control-plane primary stopped | A read | 14046 | 275 | 2.0 | 60 | 148 | 3009 |
| control-plane primary stopped | B write | 12578 | 674 | 5.4 | 75 | 183 | 3004 |
