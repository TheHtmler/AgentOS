"""add durable scheduled tasks

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "u6v7w8x9y0z1"
down_revision: str | Sequence[str] | None = "t5u6v7w8x9y0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule_type", sa.String(length=16), nullable=False),
        sa.Column("schedule_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=24), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("result_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "schedule_type IN ('once', 'daily', 'weekly', 'monthly')",
            name="ck_scheduled_tasks_schedule_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed')",
            name="ck_scheduled_tasks_status",
        ),
        sa.CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('queued', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')",
            name="ck_scheduled_tasks_last_run_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_scheduled_tasks_thread_id"),
    )
    op.create_index(
        "ix_scheduled_tasks_owner_user_id",
        "scheduled_tasks",
        ["owner_user_id"],
    )
    op.create_index("ix_scheduled_tasks_agent_id", "scheduled_tasks", ["agent_id"])
    op.create_index("ix_scheduled_tasks_case_id", "scheduled_tasks", ["case_id"])
    op.create_index("ix_scheduled_tasks_next_run_at", "scheduled_tasks", ["next_run_at"])
    op.create_index("ix_scheduled_tasks_deleted_at", "scheduled_tasks", ["deleted_at"])
    op.create_index(
        "ix_scheduled_tasks_due",
        "scheduled_tasks",
        ["status", "next_run_at"],
    )

    op.add_column(
        "runs",
        sa.Column("scheduled_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("runs", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_runs_scheduled_task_id_scheduled_tasks",
        "runs",
        "scheduled_tasks",
        ["scheduled_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_runs_scheduled_task_id", "runs", ["scheduled_task_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_scheduled_task_id", table_name="runs")
    op.drop_constraint(
        "fk_runs_scheduled_task_id_scheduled_tasks",
        "runs",
        type_="foreignkey",
    )
    op.drop_column("runs", "scheduled_for")
    op.drop_column("runs", "scheduled_task_id")
    op.drop_index("ix_scheduled_tasks_due", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_deleted_at", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_next_run_at", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_case_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_agent_id", table_name="scheduled_tasks")
    op.drop_index("ix_scheduled_tasks_owner_user_id", table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
