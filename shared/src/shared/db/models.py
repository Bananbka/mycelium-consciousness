from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from shared.db.db import Base


class CloneProfile(Base):
    __tablename__ = "clone_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    designation: Mapped[str] = mapped_column(String, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    memories: Mapped[list["MemoryChunk"]] = relationship(back_populates="clone")


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    clone_id: Mapped[int] = mapped_column(ForeignKey("clone_profiles.id"))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    timestamp: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    clone: Mapped[CloneProfile] = relationship(back_populates="memories")
