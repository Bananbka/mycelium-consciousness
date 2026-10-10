"""Make the memory data plane shardable (lab 7).

Drops the foreign keys from ``memory_buffers.clone_id`` and
``memory_backups.clone_id`` to ``clone_profiles.id``. When those tables live on
a shard, ``clone_profiles`` is on a different database and a cross-database
constraint cannot exist; referential integrity for the shard key moves into the
application (a clone's profile is always created before its data, and profiles
are not hard-deleted by any code path).

The same revision runs on every shard, so each shard has the full schema; only
the data-plane tables are populated there.
"""

from __future__ import annotations

from alembic import op

revision = "0002_shardable_data_plane"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "memory_buffers_clone_id_fkey", "memory_buffers", type_="foreignkey"
    )
    op.drop_constraint(
        "memory_backups_clone_id_fkey", "memory_backups", type_="foreignkey"
    )


def downgrade() -> None:
    op.create_foreign_key(
        "memory_buffers_clone_id_fkey",
        "memory_buffers",
        "clone_profiles",
        ["clone_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "memory_backups_clone_id_fkey",
        "memory_backups",
        "clone_profiles",
        ["clone_id"],
        ["id"],
        ondelete="CASCADE",
    )
