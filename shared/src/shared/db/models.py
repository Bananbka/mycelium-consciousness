from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from shared import backup_codec
from shared.db.db import Base
from shared.roles import UserRole
from shared.subscriptions import SubscriptionTier


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=UserRole.CLONE,
        server_default=UserRole.CLONE.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profile: Mapped["CloneProfile | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


ACTIVE_STATUS = "active"


class CloneProfile(Base):
    __tablename__ = "clone_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    designation: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String,
        default=ACTIVE_STATUS,
        server_default=ACTIVE_STATUS,
    )
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(
            SubscriptionTier,
            name="subscription_tier",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=SubscriptionTier.FREE,
        server_default=SubscriptionTier.FREE.value,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User | None"] = relationship(back_populates="profile")
    buffer: Mapped["MemoryBuffer | None"] = relationship(
        back_populates="clone",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    backups: Mapped[list["MemoryBackup"]] = relationship(
        back_populates="clone",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MemoryBuffer(Base):
    """One clone's not-yet-backed-up memory: a single updated bytea row.

    `celery_worker.flush` drains Redis into this row (decode, append, encode,
    overwrite) rather than inserting a row per write. `celery_worker.pipeline`
    reads it whole, archives it to object storage, and deletes the row — the
    next flush recreates it from scratch. There is no Redis stage skipped: a
    write still lands in Redis first; this is where it ends up once flushed.
    """

    __tablename__ = "memory_buffers"

    clone_id: Mapped[int] = mapped_column(
        ForeignKey("clone_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    payload_blob: Mapped[bytes] = mapped_column("payload", LargeBinary)
    entry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    clone: Mapped[CloneProfile] = relationship(back_populates="buffer")

    @property
    def payload(self) -> list[dict]:
        return (
            backup_codec.decode_payload(self.payload_blob) if self.entry_count else []
        )


class MemoryBackup(Base):
    """Metadata for one archived period. The payload itself lives in MinIO.

    `storage_key` points at the object; nothing here holds the bytes, so
    listing and pruning backups never touches object storage except to
    delete the object a pruned row pointed at.
    """

    __tablename__ = "memory_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clone_id: Mapped[int] = mapped_column(
        ForeignKey("clone_profiles.id", ondelete="CASCADE"),
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_count: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String)
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        Enum(
            SubscriptionTier,
            name="subscription_tier",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    clone: Mapped[CloneProfile] = relationship(back_populates="backups")

    __table_args__ = (
        Index("ix_memory_backups_clone_id_period_end", "clone_id", "period_end"),
    )
