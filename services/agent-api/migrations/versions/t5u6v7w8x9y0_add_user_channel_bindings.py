"""add AgentOS user channel bindings

Revision ID: t5u6v7w8x9y0
Revises: s5t6u7v8w9x0
Create Date: 2026-08-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t5u6v7w8x9y0"
down_revision: str | Sequence[str] | None = "s5t6u7v8w9x0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_channel_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "receive_notifications",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "allow_openclaw",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "allow_agentos",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
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
            name="ck_user_channel_bindings_channel",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_user_channel_bindings_status",
        ),
        sa.CheckConstraint(
            "length(trim(account_id)) > 0",
            name="ck_user_channel_bindings_account_id",
        ),
        sa.CheckConstraint(
            "length(trim(peer_id)) > 0",
            name="ck_user_channel_bindings_peer_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            name="uq_user_channel_bindings_endpoint",
        ),
    )
    op.create_index(
        "ix_user_channel_bindings_user_id",
        "user_channel_bindings",
        ["user_id"],
    )
    op.create_index(
        "ix_user_channel_bindings_user_channel",
        "user_channel_bindings",
        ["user_id", "channel"],
    )
    op.create_index(
        "uq_user_channel_bindings_default",
        "user_channel_bindings",
        ["user_id", "channel"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_channel_bindings_default",
        table_name="user_channel_bindings",
    )
    op.drop_index(
        "ix_user_channel_bindings_user_channel",
        table_name="user_channel_bindings",
    )
    op.drop_index(
        "ix_user_channel_bindings_user_id",
        table_name="user_channel_bindings",
    )
    op.drop_table("user_channel_bindings")
