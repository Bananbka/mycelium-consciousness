"""Create initial clone memory tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "clone_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("designation", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clone_profiles_id", "clone_profiles", ["id"])
    op.create_index(
        "ix_clone_profiles_designation",
        "clone_profiles",
        ["designation"],
        unique=True,
    )
    op.create_table(
        "memory_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clone_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=768), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["clone_id"], ["clone_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_chunks_id", "memory_chunks", ["id"])


def downgrade() -> None:
    op.drop_index("ix_memory_chunks_id", table_name="memory_chunks")
    op.drop_table("memory_chunks")
    op.drop_index("ix_clone_profiles_designation", table_name="clone_profiles")
    op.drop_index("ix_clone_profiles_id", table_name="clone_profiles")
    op.drop_table("clone_profiles")
