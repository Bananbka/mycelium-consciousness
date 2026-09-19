from __future__ import annotations

import os

import pytest
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# At least 32 bytes, or PyJWT warns the HMAC key is too short for SHA256.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-pytest-only-0123456789")

from api.main import app  # noqa: E402
from api.security import hash_password  # noqa: E402
from shared import cache, object_storage, streams  # noqa: E402
from shared.db.db import get_db  # noqa: E402
from shared.db.models import Base, CloneProfile, User, UserRole  # noqa: E402


def _test_database_url() -> str:
    """Resolve the test database, defaulting to a dedicated `*_test` database.

    This suite drops and recreates every table, so it must never be pointed at
    a database holding real data. The default appends `_test` to POSTGRES_DB
    rather than reusing it directly.
    """
    if url := os.getenv("TEST_DATABASE_URL"):
        return url

    user = os.getenv("POSTGRES_USER", "admin")
    password = os.getenv("POSTGRES_PASSWORD", "secretpassword")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "clone_memory") + "_test"
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def _guard_destructive_target(url: str) -> None:
    name = _database_name(url)
    if "test" in name.lower():
        return
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_DB") == "1":
        return
    pytest.exit(
        f"Refusing to run: the test suite drops every table, and {name!r} is "
        "not a test database. Use a name containing 'test', set "
        "TEST_DATABASE_URL, or set ALLOW_DESTRUCTIVE_TEST_DB=1 to override.",
        returncode=4,
    )


async def _ensure_database_exists(url: str) -> None:
    target = _database_name(url)
    server_url = url.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target},
            )
            if not exists:
                await conn.exec_driver_sql(f'CREATE DATABASE "{target}"')
    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session")
async def engine():
    url = _test_database_url()
    _guard_destructive_target(url)
    await _ensure_database_exists(url)

    engine = create_async_engine(url, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine):
    """One transaction per test, rolled back afterwards.

    The app's get_db is overridden with a session bound to this same
    connection, so request handlers and assertions observe identical state and
    nothing leaks between tests.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            # Route handlers call commit(); without this the outer transaction
            # would end and the rollback below would have nothing to undo.
            join_transaction_mode="create_savepoint",
        )
        db = maker()

        yield db

        await db.close()
        await transaction.rollback()


def _guard_destructive_bucket() -> None:
    bucket = object_storage.MINIO_BUCKET
    if "test" in bucket.lower():
        return
    if os.getenv("ALLOW_DESTRUCTIVE_TEST_BUCKET") == "1":
        return
    pytest.exit(
        f"Refusing to run: the test suite wipes the whole MinIO bucket, and "
        f"{bucket!r} is not a test bucket. Set MINIO_BUCKET to something "
        "containing 'test', or set ALLOW_DESTRUCTIVE_TEST_BUCKET=1 to override.",
        returncode=4,
    )


@pytest.fixture(autouse=True)
async def clean_object_storage():
    _guard_destructive_bucket()
    await object_storage.ensure_bucket()

    async def _clear() -> None:
        for key in await object_storage.list_keys():
            await object_storage.delete_object(key)

    await _clear()
    yield
    await _clear()


@pytest.fixture(autouse=True)
async def clean_redis_streams():
    client = streams.get_redis()

    async def _clear() -> None:
        async for key in client.scan_iter(match=f"{streams.STREAM_KEY_PREFIX}*"):
            await client.delete(key)
        cache_client = redis.from_url(cache.CACHE_REDIS_URL, decode_responses=True)
        try:
            async for key in cache_client.scan_iter(match="cache:*"):
                await cache_client.delete(key)
        finally:
            await cache_client.aclose()

    await _clear()
    yield
    await _clear()


@pytest.fixture
async def client(session):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http

    app.dependency_overrides.clear()


async def _make_user(
    session,
    email: str,
    password: str,
    role: UserRole,
    designation: str | None = None,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    if designation is not None:
        user.profile = CloneProfile(designation=designation)

    session.add(user)
    await session.flush()
    return user


async def _token(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_user(session) -> User:
    return await _make_user(
        session, "admin@clones.example.com", "adminpass123", UserRole.ADMIN
    )


@pytest.fixture
async def clone_user(session) -> User:
    return await _make_user(
        session,
        "alpha@clones.example.com",
        "alphapass123",
        UserRole.CLONE,
        designation="clone-alpha",
    )


@pytest.fixture
async def other_clone_user(session) -> User:
    return await _make_user(
        session,
        "beta@clones.example.com",
        "betapass123",
        UserRole.CLONE,
        designation="clone-beta",
    )


@pytest.fixture
async def admin_headers(client, admin_user) -> dict[str, str]:
    return _auth(await _token(client, "admin@clones.example.com", "adminpass123"))


@pytest.fixture
async def clone_headers(client, clone_user) -> dict[str, str]:
    return _auth(await _token(client, "alpha@clones.example.com", "alphapass123"))


@pytest.fixture
async def other_clone_headers(client, other_clone_user) -> dict[str, str]:
    return _auth(await _token(client, "beta@clones.example.com", "betapass123"))
