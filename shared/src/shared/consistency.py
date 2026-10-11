"""Lab 6: read/write routing state shared by every API instance.

Two small pieces, both deliberately outside process memory (lab 2: stateless):

* **Read-after-write window** (``mark_*`` / ``recently_wrote``): a short-lived
  Redis flag per writer. While it exists, that writer's reads go to the primary,
  so a client never observes a replica that has not replayed its own write yet.
* **Replica circuit breaker** (``ReplicaBreaker``): remembers, per process, that
  the standby just failed so the next requests skip it instead of each paying
  the connect timeout. It only caches a health observation; losing it on a
  restart costs one probe, never correctness.
"""

from __future__ import annotations

import logging
import os
import socket
import time

import asyncpg.exceptions as pg_exc
from sqlalchemy.exc import DBAPIError, InterfaceError, InternalError, OperationalError

from shared import cache

logger = logging.getLogger(__name__)

# Must exceed the replica's worst realistic replay lag, otherwise the window
# closes before the standby has caught up. Measured lag on this stack is
# milliseconds; 5s leaves two orders of magnitude of headroom.
STICKY_SECONDS = int(os.getenv("REPLICA_STICKY_SECONDS", "5"))
# How long a failed replica is skipped before the next probe.
BREAKER_COOLDOWN_SECONDS = float(os.getenv("REPLICA_BREAKER_COOLDOWN_SECONDS", "5"))

KEY_VERSION = "v1"

# Raised by the driver when a node is unreachable. asyncpg connect failures
# surface as plain OSError subclasses; failures on an established connection
# are wrapped by SQLAlchemy. ``InternalError`` is what asyncpg's "cannot switch
# to state N; another operation is in progress" becomes: the client-side
# symptom of several requests discovering the same dead pooled connection at
# once (lab 10 saw it when the primary was stopped with pre-ping off).
CONNECTIVITY_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    socket.gaierror,
    TimeoutError,
    OperationalError,
    InterfaceError,
    InternalError,
    # raised unwrapped while *connecting* ("the database system is shutting
    # down") or surfacing as a bare DBAPIError when an established connection
    # is closed in the middle of an operation
    DBAPIError,
    pg_exc.PostgresConnectionError,
    pg_exc.CannotConnectNowError,
    pg_exc.InterfaceError,
)


def _user_key(user_id: int) -> str:
    return f"rw:{KEY_VERSION}:user:{user_id}"


def _clone_key(clone_id: int) -> str:
    return f"rw:{KEY_VERSION}:clone:{clone_id}"


async def _mark(key: str) -> None:
    try:
        await cache._get_client().set(key, "1", ex=STICKY_SECONDS)
    except Exception:
        logger.warning("read-after-write mark unavailable for %s", key)


async def mark_user_write(user_id: int) -> None:
    await _mark(_user_key(user_id))


async def mark_clone_write(clone_id: int) -> None:
    """A write the clone itself did not make (rollup, admin) changed its rows."""
    await _mark(_clone_key(clone_id))


async def recently_wrote(user_id: int, clone_id: int | None = None) -> bool:
    """True when this reader must see the primary. Fails closed (True)."""
    keys = [_user_key(user_id)]
    if clone_id is not None:
        keys.append(_clone_key(clone_id))
    try:
        return await cache._get_client().exists(*keys) > 0
    except Exception:
        logger.warning("read-after-write lookup unavailable, using primary")
        return True


class ReplicaBreaker:
    def __init__(self, cooldown: float = BREAKER_COOLDOWN_SECONDS) -> None:
        self.cooldown = cooldown
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        return time.monotonic() < self._open_until

    def trip(self) -> None:
        self._open_until = time.monotonic() + self.cooldown

    def reset(self) -> None:
        self._open_until = 0.0


replica_breaker = ReplicaBreaker()
