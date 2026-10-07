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
        self._creds = random.choice(_accounts)
        if not self._login("POST /auth/login"):
            self.stop()

    def _login(self, name: str) -> bool:
        with self.client.post(
            "/auth/login",
            json=self._creds,
            name=name,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login failed: {response.status_code}")
                return False
            token = response.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"
        return True

    def _api(self, method: str, path: str, name: str, **kwargs) -> None:
        """Call the API; when the access token has expired, log in again and retry.

        A real client renews its token instead of failing for the rest of the
        session. The 401 itself is not hidden: the renewal shows up as its own
        `POST /auth/login (renew)` row, which a soak test needs because every
        user logged in during the ramp-up and tokens expire together.
        """
        with self.client.request(
            method, path, name=name, catch_response=True, **kwargs
        ) as response:
            if response.status_code != 401:
                return
            response.success()
        if self._login("POST /auth/login (renew)"):
            self.client.request(method, path, name=name, **kwargs)

    @task(10)
    def write_memory(self) -> None:
        self._api(
            "POST",
            "/memories/write",
            "POST /memories/write",
            json={"content": _noise()},
        )

    @task(3)
    def read_profile(self) -> None:
        self._api("GET", "/profiles/me", "GET /profiles/me")

    @task(2)
    def list_backups(self) -> None:
        self._api("GET", "/memories/backups", "GET /memories/backups")

    @task(1)
    def stream_status(self) -> None:
        self._api("GET", "/memories/stream/status", "GET /memories/stream/status")

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="GET /health")
