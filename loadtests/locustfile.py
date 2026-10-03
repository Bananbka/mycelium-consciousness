from __future__ import annotations

import json
import os
import random
import string
import sys
from pathlib import Path

from locust import HttpUser, between, task

sys.path.insert(0, str(Path(__file__).parent))

if os.getenv("LOAD_PROFILE"):
    from profiles import PROFILES, build_shape

    ProfileShape = build_shape(PROFILES[os.environ["LOAD_PROFILE"]])

# Credentials written by loadtests/provision.py.
_ACCOUNTS_FILE = Path(os.getenv("LOAD_ACCOUNTS_FILE", "results/accounts.json"))
_accounts: list[dict] = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
if not _accounts:
    raise SystemExit(f"no accounts in {_ACCOUNTS_FILE}; run loadtests/provision.py")

_ALPHABET = string.ascii_letters + string.digits + "     "


def _noise(low: int = 64, high: int = 1024) -> str:
    return "".join(random.choices(_ALPHABET, k=random.randint(low, high)))


class CloneImplant(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        with self.client.post(
            "/auth/login",
            json=random.choice(_accounts),
            name="POST /auth/login",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login failed: {response.status_code}")
                self.stop()
                return
            token = response.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    @task(10)
    def write_memory(self) -> None:
        self.client.post(
            "/memories/write",
            json={"content": _noise()},
            name="POST /memories/write",
        )

    @task(3)
    def read_profile(self) -> None:
        self.client.get("/profiles/me", name="GET /profiles/me")

    @task(2)
    def list_backups(self) -> None:
        self.client.get("/memories/backups", name="GET /memories/backups")

    @task(1)
    def stream_status(self) -> None:
        self.client.get("/memories/stream/status", name="GET /memories/stream/status")

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="GET /health")
