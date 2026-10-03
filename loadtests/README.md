# Load tests (Locust)

[Locust](https://locust.io) drives a scenario written in Python
(`locustfile.py`): each virtual user is a clone implant that logs in once and
then loops over `POST /memories/write` (weight 10), `GET /profiles/me` (3),
`GET /memories/backups` (2), `GET /memories/stream/status` (1) and
`GET /health` (1), with 0.2–1.0 s of think time. Accounts are registered up
front, so the measured window is steady-state; each user's login is still
measured as a real session start.

Profiles (`profiles.py`):

| Profile | Users | Ramp-up | Duration |
| --- | --- | --- | --- |
| `baseline` | 50 | 30 s | 3 min |
| `step` | 50 → 600, +50 every 45 s | 10 users/s per step | 9 min |

Run against a **sandbox** stack only — never production (the scenario creates
accounts):

```bash
cp infra/.env.sandbox.example infra/.env
docker compose -f infra/docker-compose.yaml up -d --build

uv sync --group load
loadtests/run.sh baseline            # client metrics + docker CPU/RAM
loadtests/run.sh step
uv run --group load python loadtests/make_report.py baseline step
```

`run.sh` takes optional `[host] [compose-project]` (defaults
`http://localhost:8088`, `clone-sandbox`). Raw CSVs and Locust's own HTML report
land in `results/` (gitignored); charts and summary tables are written to
`docs/load-testing/`. For interactive exploration drop `--headless` and open
`http://localhost:8089`:

```bash
uv run --group load locust -f loadtests/locustfile.py --host http://localhost:8088
```
