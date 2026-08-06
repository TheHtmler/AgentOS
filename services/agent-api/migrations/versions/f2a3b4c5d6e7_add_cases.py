"""add generic cases and case_enabled flag

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-06 16:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_cases_status",
        ),
    )
    op.create_index("ix_cases_owner_user_id", "cases", ["owner_user_id"])

    op.create_table(
        "case_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('owner')", name="ck_case_memberships_role"),
        sa.UniqueConstraint(
            "case_id",
            "user_id",
            name="uq_case_memberships_case_user",
        ),
    )
    op.create_index("ix_case_memberships_case_id", "case_memberships", ["case_id"])
    op.create_index("ix_case_memberships_user_id", "case_memberships", ["user_id"])

    op.create_table(
        "case_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'proposed'"),
            nullable=False,
        ),
        sa.Column(
            "source_thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'archived')",
            name="ck_case_facts_status",
        ),
    )
    op.create_index("ix_case_facts_case_id", "case_facts", ["case_id"])
    op.create_index("ix_case_facts_case_status", "case_facts", ["case_id", "status"])

    op.create_table(
        "user_agent_default_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "agent_id",
            name="uq_user_agent_default_cases_user_agent",
        ),
    )
    op.create_index(
        "ix_user_agent_default_cases_user_id",
        "user_agent_default_cases",
        ["user_id"],
    )
    op.create_index(
        "ix_user_agent_default_cases_agent_id",
        "user_agent_default_cases",
        ["agent_id"],
    )
    op.create_index(
        "ix_user_agent_default_cases_case_id",
        "user_agent_default_cases",
        ["case_id"],
    )

    op.add_column(
        "threads",
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_threads_case_id", "threads", ["case_id"])

    op.add_column(
        "agent_versions",
        sa.Column(
            "case_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_versions AS av
            SET case_enabled = true
            FROM agents AS a
            WHERE a.id = av.agent_id
              AND a.slug = 'imd'
            """,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "case_enabled")
    op.drop_index("ix_threads_case_id", table_name="threads")
    op.drop_column("threads", "case_id")
    op.drop_index("ix_user_agent_default_cases_case_id", table_name="user_agent_default_cases")
    op.drop_index("ix_user_agent_default_cases_agent_id", table_name="user_agent_default_cases")
    op.drop_index("ix_user_agent_default_cases_user_id", table_name="user_agent_default_cases")
    op.drop_table("user_agent_default_cases")
    op.drop_index("ix_case_facts_case_status", table_name="case_facts")
    op.drop_index("ix_case_facts_case_id", table_name="case_facts")
    op.drop_table("case_facts")
    op.drop_index("ix_case_memberships_user_id", table_name="case_memberships")
    op.drop_index("ix_case_memberships_case_id", table_name="case_memberships")
    op.drop_table("case_memberships")
    op.drop_index("ix_cases_owner_user_id", table_name="cases")
    op.drop_table("cases")
