"""Initial schema: users, roles, clone profiles, memory buffers, backups.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("admin", "clone")
TIERS = ("free", "standard", "premium")


def upgrade() -> None:
    postgresql.ENUM(*ROLES, name="user_role").create(op.get_bind(), checkfirst=True)
    postgresql.ENUM(*TIERS, name="subscription_tier").create(
        op.get_bind(), checkfirst=True
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(*ROLES, name="user_role", create_type=False),
            nullable=False,
            server_default="clone",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "clone_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("designation", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "subscription_tier",
            postgresql.ENUM(*TIERS, name="subscription_tier", create_type=False),
            nullable=False,
            server_default="free",
        ),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_clone_profiles_designation",
        "clone_profiles",
        ["designation"],
        unique=True,
    )
    op.create_unique_constraint(
        "uq_clone_profiles_user_id", "clone_profiles", ["user_id"]
    )

    op.create_table(
        "memory_buffers",
        sa.Column("clone_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["clone_id"], ["clone_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("clone_id"),
    )

    op.create_table(
        "memory_backups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clone_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column(
            "subscription_tier",
            postgresql.ENUM(*TIERS, name="subscription_tier", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["clone_id"], ["clone_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_backups_clone_id_period_end",
        "memory_backups",
        ["clone_id", "period_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_backups_clone_id_period_end", table_name="memory_backups")
    op.drop_table("memory_backups")

    op.drop_table("memory_buffers")

    op.drop_constraint("uq_clone_profiles_user_id", "clone_profiles", type_="unique")
    op.drop_index("ix_clone_profiles_designation", table_name="clone_profiles")
    op.drop_table("clone_profiles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    postgresql.ENUM(*TIERS, name="subscription_tier").drop(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM(*ROLES, name="user_role").drop(op.get_bind(), checkfirst=True)
