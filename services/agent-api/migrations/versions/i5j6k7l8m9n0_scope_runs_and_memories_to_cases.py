"""scope runs and user memories to Cases

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-08-11 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "i5j6k7l8m9n0"
down_revision: str | Sequence[str] | None = "h4i5j6k7l8m9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_runs_case_id_cases",
        "runs",
        "cases",
        ["case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_runs_case_id", "runs", ["case_id"])
    op.execute(
        sa.text(
            """
            UPDATE runs AS r
            SET case_id = t.case_id
            FROM threads AS t
            WHERE r.thread_id = t.id
            """,
        ),
    )

    op.add_column(
        "user_memories",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_memories_case_id_cases",
        "user_memories",
        "cases",
        ["case_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_user_memories_user_agent_case_status",
        "user_memories",
        ["user_id", "agent_id", "case_id", "status"],
    )
    op.drop_index("uq_user_memories_active_profile_key", table_name="user_memories")
    op.create_index(
        "uq_user_memories_active_global_profile_key",
        "user_memories",
        ["user_id", "agent_id", "key"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'profile' AND status = 'active' AND key IS NOT NULL AND case_id IS NULL",
        ),
    )
    op.create_index(
        "uq_user_memories_active_case_profile_key",
        "user_memories",
        ["user_id", "agent_id", "case_id", "key"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'profile' AND status = 'active' AND key IS NOT NULL AND case_id IS NOT NULL",
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_memories_active_case_profile_key",
        table_name="user_memories",
    )
    op.drop_index(
        "uq_user_memories_active_global_profile_key",
        table_name="user_memories",
    )
    op.create_index(
        "uq_user_memories_active_profile_key",
        "user_memories",
        ["user_id", "agent_id", "key"],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'profile' AND status = 'active' AND key IS NOT NULL",
        ),
    )
    op.drop_index(
        "ix_user_memories_user_agent_case_status",
        table_name="user_memories",
    )
    op.drop_constraint(
        "fk_user_memories_case_id_cases",
        "user_memories",
        type_="foreignkey",
    )
    op.drop_column("user_memories", "case_id")

    op.drop_index("ix_runs_case_id", table_name="runs")
    op.drop_constraint("fk_runs_case_id_cases", "runs", type_="foreignkey")
    op.drop_column("runs", "case_id")
