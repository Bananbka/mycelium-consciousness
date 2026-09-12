"""Create or promote an administrator account.

Admins are deliberately not self-registerable over HTTP, so they are seeded out
of band:

    uv run --package api seed-admin admin@example.com hunter2hunter2
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from api.security import hash_password
from shared.db.db import AsyncSessionLocal, dispose_engine
from shared.db.models import User, UserRole


async def seed_admin(email: str, password: str) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()

        if user is None:
            session.add(
                User(
                    email=email.lower(),
                    password_hash=hash_password(password),
                    role=UserRole.ADMIN,
                )
            )
            outcome = f"created admin {email}"
        else:
            user.role = UserRole.ADMIN
            user.password_hash = hash_password(password)
            user.is_active = True
            outcome = f"promoted {email} to admin"

        await session.commit()

    return outcome


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: seed-admin <email> <password>", file=sys.stderr)
        raise SystemExit(2)

    email, password = sys.argv[1], sys.argv[2]
    if len(password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        raise SystemExit(2)

    async def run() -> None:
        print(await seed_admin(email, password))
        await dispose_engine()

    asyncio.run(run())
