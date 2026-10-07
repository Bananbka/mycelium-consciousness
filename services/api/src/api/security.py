from __future__ import annotations

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi.concurrency import run_in_threadpool

from shared.auth import create_access_token, decode_access_token
from shared.settings import settings

__all__ = [
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "hash_password_async",
    "verify_password",
    "verify_password_async",
]

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# argon2 is deliberately slow (~100+ ms of CPU, 64 MiB). Calling it directly in
# an async handler freezes the event loop, so every other request on the worker
# stalls behind each login. It runs in a worker thread instead (argon2-cffi
# releases the GIL), with a cap so a login burst cannot hold dozens of 64 MiB
# hashes in memory at once.
_slots = threading.BoundedSemaphore(settings.password_hash_concurrency)


def _bounded(fn, *args):
    with _slots:
        return fn(*args)


async def hash_password_async(password: str) -> str:
    return await run_in_threadpool(_bounded, hash_password, password)


async def verify_password_async(password: str, password_hash: str) -> bool:
    return await run_in_threadpool(_bounded, verify_password, password, password_hash)
