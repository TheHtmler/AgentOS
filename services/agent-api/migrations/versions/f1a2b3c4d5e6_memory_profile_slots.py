"""add memory kind/key profile slots

Revision ID: f1a2b3c4d5e6
Revises: f0a1b2c3d4e5
Create Date: 2026-08-05 20:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_memories",
        sa.Column(
            "kind",
            sa.String(length=16),
            server_default=sa.text("'note'"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_memories",
        sa.Column("key", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_memories_kind",
        "user_memories",
        "kind IN ('profile', 'note')",
    )
    op.create_index(
        "ix_user_memories_user_agent_kind_status",
        "user_memories",
        ["user_id", "agent_id", "kind", "status"],
    )
    # One active profile slot per (user, agent, key).
    op.create_index(
        "uq_user_memories_active_profile_key",
        "user_memories",
        ["user_id", "agent_id", "key"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'profile' AND status = 'active' AND key IS NOT NULL",
        ),
    )

    # Best-effort backfill: keep newest row per user×agent×slot, archive the rest.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY user_id, agent_id
                       ORDER BY updated_at DESC
                     ) AS rn
              FROM user_memories
              WHERE status = 'active'
                AND key IS NULL
                AND tags @> ARRAY['身高']::text[]
            )
            UPDATE user_memories AS um
            SET kind = 'profile', key = 'height_cm'
            FROM ranked
            WHERE um.id = ranked.id AND ranked.rn = 1
            """,
        ),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY user_id, agent_id
                       ORDER BY updated_at DESC
                     ) AS rn
              FROM user_memories
              WHERE status = 'active'
                AND key IS NULL
                AND tags @> ARRAY['身高']::text[]
            )
            UPDATE user_memories AS um
            SET status = 'archived'
            FROM ranked
            WHERE um.id = ranked.id AND ranked.rn > 1
            """,
        ),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY user_id, agent_id
                       ORDER BY updated_at DESC
                     ) AS rn
              FROM user_memories
              WHERE status = 'active'
                AND key IS NULL
                AND tags @> ARRAY['体重']::text[]
            )
            UPDATE user_memories AS um
            SET kind = 'profile', key = 'weight_kg'
            FROM ranked
            WHERE um.id = ranked.id AND ranked.rn = 1
            """,
        ),
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY user_id, agent_id
                       ORDER BY updated_at DESC
                     ) AS rn
              FROM user_memories
              WHERE status = 'active'
                AND key IS NULL
                AND tags @> ARRAY['体重']::text[]
            )
            UPDATE user_memories AS um
            SET status = 'archived'
            FROM ranked
            WHERE um.id = ranked.id AND ranked.rn > 1
            """,
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_user_memories_active_profile_key", table_name="user_memories")
    op.drop_index("ix_user_memories_user_agent_kind_status", table_name="user_memories")
    op.drop_constraint("ck_user_memories_kind", "user_memories", type_="check")
    op.drop_column("user_memories", "key")
    op.drop_column("user_memories", "kind")
