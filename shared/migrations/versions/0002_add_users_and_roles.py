"""Add users, roles, and the clone profile owner link.

Revision ID: 0002_add_users_and_roles
Revises: 0001_initial_schema
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_add_users_and_roles"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("admin", "clone")

# create_type=False stops create_table from emitting a second CREATE TYPE on
# top of the explicit one below, which fails with DuplicateObject.
user_role_column = postgresql.ENUM(*ROLES, name="user_role", create_type=False)


def upgrade() -> None:
    postgresql.ENUM(*ROLES, name="user_role").create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            user_role_column,
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

    op.add_column(
        "clone_profiles",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_clone_profiles_user_id",
        "clone_profiles",
        ["user_id"],
    )
    op.create_foreign_key(
        "fk_clone_profiles_user_id_users",
        "clone_profiles",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_clone_profiles_user_id_users",
        "clone_profiles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_clone_profiles_user_id",
        "clone_profiles",
        type_="unique",
    )
    op.drop_column("clone_profiles", "user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    postgresql.ENUM(*ROLES, name="user_role").drop(op.get_bind(), checkfirst=True)
