import logging
import os
from collections.abc import AsyncIterator

import asyncpg.exceptions as pg_exc
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

POSTGRES_USER = os.getenv("POSTGRES_USER", "admin")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "secretpassword")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "clone_memory_db")

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
POOL_RECYCLE_SECONDS = int(os.getenv("DB_POOL_RECYCLE", "1800"))
# Bounds how long a request waits on an unreachable node before the routing
# layer fails over (lab 6); asyncpg's own default is 60s.
CONNECT_TIMEOUT_SECONDS = float(os.getenv("DB_CONNECT_TIMEOUT", "3"))
# Lab 9: pre-ping spends a BEGIN/ROLLBACK round trip on *every* checkout. The
# profile showed it as one of five round trips behind a request that needs one,
# so it is off by default; a dead connection is invalidated on first use and
# surfaces as a 503 (lab 6 handler) instead of being probed ahead of time.
POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "0") == "1"

DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Lab 6: an optional streaming-replication standby for read traffic. Unset
# means "single node": every read goes to the primary, nothing else changes.
POSTGRES_REPLICA_HOST = os.getenv("POSTGRES_REPLICA_HOST", "")
POSTGRES_REPLICA_PORT = os.getenv("POSTGRES_REPLICA_PORT", POSTGRES_PORT)
REPLICA_ENABLED = bool(POSTGRES_REPLICA_HOST)
REPLICA_POOL_SIZE = int(os.getenv("DB_REPLICA_POOL_SIZE", str(POOL_SIZE)))

REPLICA_DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_REPLICA_HOST}:{POSTGRES_REPLICA_PORT}/{POSTGRES_DB}"
)


logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class TolerantSession(AsyncSession):
    """``close()`` that survives a connection lost mid-request (lab 10).

    Closing rolls the transaction back on the wire. When the node just went
    away that rollback raises - and because it runs in the dependency's
    teardown, *after* the response was produced, nothing can turn it into a
    503: the exception escapes the ASGI app and the connection towards nginx is
    reset, which nginx counts as an upstream failure. The pool invalidates the
    broken connection itself, so swallowing the error here loses nothing.
    """

    async def close(self) -> None:
        try:
            await super().close()
        except (
            DBAPIError,
            pg_exc.PostgresConnectionError,
            pg_exc.InterfaceError,
            pg_exc.InternalClientError,
            OSError,
        ):
            logger.warning("session close failed, connection discarded", exc_info=True)


engine = create_async_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_pre_ping=POOL_PRE_PING,
    pool_recycle=POOL_RECYCLE_SECONDS,
    connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    echo=False,
)

replica_engine = (
    create_async_engine(
        REPLICA_DATABASE_URL,
        pool_size=REPLICA_POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=POOL_PRE_PING,
        pool_recycle=POOL_RECYCLE_SECONDS,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
        echo=False,
    )
    if REPLICA_ENABLED
    else None
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=TolerantSession,
    expire_on_commit=False,
    autoflush=False,
)

# Read-only sessions against the standby; None when no replica is configured.
ReplicaSessionLocal = (
    async_sessionmaker(
        bind=replica_engine,
        class_=TolerantSession,
        expire_on_commit=False,
        autoflush=False,
    )
    if replica_engine is not None
    else None
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()
    if replica_engine is not None:
        await replica_engine.dispose()
