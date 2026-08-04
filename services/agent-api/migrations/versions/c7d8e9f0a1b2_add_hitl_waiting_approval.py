"""add waiting_approval run status and interrupts table

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status",
        "runs",
        "status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')",
    )
    op.drop_index(
        "uq_runs_one_running_per_thread",
        table_name="runs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_runs_one_running_per_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'waiting_approval')"),
    )
    op.create_table(
        "interrupts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("tool_args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decision_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'timed_out', 'cancelled')",
            name="ck_interrupts_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_interrupts_run_tool_call"),
    )
    op.create_index("ix_interrupts_run_id", "interrupts", ["run_id"])
    op.create_index(
        "ix_interrupts_pending_expires",
        "interrupts",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interrupts_pending_expires",
        table_name="interrupts",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index("ix_interrupts_run_id", table_name="interrupts")
    op.drop_table("interrupts")
    op.drop_index(
        "uq_runs_one_running_per_thread",
        table_name="runs",
        postgresql_where=sa.text("status IN ('running', 'waiting_approval')"),
    )
    op.create_index(
        "uq_runs_one_running_per_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status",
        "runs",
        "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
    )
