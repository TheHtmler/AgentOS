"""add OpenClaw binding pairing state

Revision ID: u6v7w8x9y0
Revises: u6v7w8x9y0z1
Create Date: 2026-08-29 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "u6v7w8x9y0"
down_revision: str | Sequence[str] | None = "u6v7w8x9y0z1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("handle", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_users_handle",
        "users",
        "handle IS NULL OR length(trim(handle)) > 0",
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_users_handle_ci ON users (lower(handle)) WHERE handle IS NOT NULL",
    )

    op.create_table(
        "channel_binding_flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.String(length=512), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("candidate_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_handle", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_flows_channel",
        ),
        sa.CheckConstraint(
            "action IN ('bind', 'unbind')",
            name="ck_channel_binding_flows_action",
        ),
        sa.CheckConstraint(
            "step IN ('awaiting_handle', 'awaiting_code', 'awaiting_target')",
            name="ck_channel_binding_flows_step",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            name="uq_channel_binding_flows_endpoint",
        ),
    )
    op.create_index(
        "ix_channel_binding_flows_expires_at",
        "channel_binding_flows",
        ["expires_at"],
    )

    op.create_table(
        "channel_binding_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_invites_channel",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("code_hash", name="uq_channel_binding_invites_code_hash"),
    )
    op.create_index(
        "ix_channel_binding_invites_user_id",
        "channel_binding_invites",
        ["user_id"],
    )
    op.create_index(
        "ix_channel_binding_invites_user_channel",
        "channel_binding_invites",
        ["user_id", "channel"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_channel_binding_invites_user_channel",
        table_name="channel_binding_invites",
    )
    op.drop_index("ix_channel_binding_invites_user_id", table_name="channel_binding_invites")
    op.drop_table("channel_binding_invites")
    op.drop_index("ix_channel_binding_flows_expires_at", table_name="channel_binding_flows")
    op.drop_table("channel_binding_flows")
    op.execute("DROP INDEX uq_users_handle_ci")
    op.drop_constraint("ck_users_handle", "users", type_="check")
    op.drop_column("users", "handle")
