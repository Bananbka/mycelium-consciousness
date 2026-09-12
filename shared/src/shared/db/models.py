import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from shared.db.db import Base

EMBEDDING_DIM = 768


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    CLONE = "clone"


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


class CloneProfile(Base):
    __tablename__ = "clone_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    designation: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String,
        default="active",
        server_default="active",
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
    memories: Mapped[list["MemoryChunk"]] = relationship(
        back_populates="clone",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE; without this SQLAlchemy
        # would SELECT every chunk into Python and emit one DELETE per row.
        passive_deletes=True,
    )


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clone_id: Mapped[int] = mapped_column(
        ForeignKey("clone_profiles.id", ondelete="CASCADE"),
    )
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    clone: Mapped[CloneProfile] = relationship(back_populates="memories")

    __table_args__ = (
        Index(
            "ix_memory_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_memory_chunks_clone_id_timestamp", "clone_id", "timestamp"),
    )
