"""add scheduled task Weixin notification outbox

Revision ID: w8x9y0z1a2b
Revises: v7w8x9y0z1a, u6v7w8x9y0z1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w8x9y0z1a2b"
down_revision: str | Sequence[str] | None = ("v7w8x9y0z1a", "u6v7w8x9y0z1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduled_tasks",
        sa.Column(
            "notification_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "scheduled_tasks", sa.Column("notification_channel", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "scheduled_tasks",
        sa.Column("notification_binding_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "scheduled_tasks",
        sa.Column("notification_last_status", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "scheduled_tasks",
        sa.Column("notification_last_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "scheduled_tasks",
        sa.Column("notification_last_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_scheduled_tasks_notification_binding",
        "scheduled_tasks",
        "user_channel_bindings",
        ["notification_binding_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scheduled_tasks_notification_binding_id", "scheduled_tasks", ["notification_binding_id"]
    )

    op.create_table(
        "scheduled_task_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), unique=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.String(length=512), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("openclaw_message_id", sa.String(length=255), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["task_id"], ["scheduled_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["binding_id"], ["user_channel_bindings.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "channel IN ('openclaw-weixin')", name="ck_scheduled_task_notifications_channel"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'retrying', 'delivered', "
            "'skipped', 'failed', 'unknown')",
            name="ck_scheduled_task_notifications_status",
        ),
        sa.UniqueConstraint(
            "run_id", "channel", name="uq_scheduled_task_notifications_run_channel"
        ),
    )
    op.create_index(
        "ix_scheduled_task_notifications_due",
        "scheduled_task_notifications",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_notifications_due", table_name="scheduled_task_notifications")
    op.drop_table("scheduled_task_notifications")
    op.drop_index("ix_scheduled_tasks_notification_binding_id", table_name="scheduled_tasks")
    op.drop_constraint(
        "fk_scheduled_tasks_notification_binding", "scheduled_tasks", type_="foreignkey"
    )
    for column in (
        "notification_last_at",
        "notification_last_error_code",
        "notification_last_status",
        "notification_binding_id",
        "notification_channel",
        "notification_enabled",
    ):
        op.drop_column("scheduled_tasks", column)
