from __future__ import annotations

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

PASSWORD = "load-test-password-1"


def register(index: int, host: str, run_id: str) -> dict | None:
    email = f"load-{run_id}-{index}@loadtest.example.com"
    response = requests.post(
        f"{host}/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "designation": f"load-{run_id}-{index}",
        },
        timeout=120,
    )
    return (
        {"email": email, "password": PASSWORD} if response.status_code == 201 else None
    )


def main() -> None:
    host, count = sys.argv[1], int(sys.argv[2])
    out = Path(sys.argv[3] if len(sys.argv) > 3 else "results/accounts.json")
    run_id = uuid.uuid4().hex[:8]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: register(i, host, run_id), range(count)))
    accounts = [r for r in results if r]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(accounts), encoding="utf-8")
    print(f"provisioned {len(accounts)}/{count} accounts -> {out}")
    if not accounts:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
