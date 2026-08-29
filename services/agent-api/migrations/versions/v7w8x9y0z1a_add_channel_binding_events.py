"""add idempotency records for external channel control events

Revision ID: v7w8x9y0z1a
Revises: u6v7w8x9y0
Create Date: 2026-08-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "v7w8x9y0z1a"
down_revision: str | Sequence[str] | None = "u6v7w8x9y0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_channel_binding_flows_step",
        "channel_binding_flows",
        type_="check",
    )
    op.create_check_constraint(
        "ck_channel_binding_flows_step",
        "channel_binding_flows",
        "step IN ('awaiting_handle', 'awaiting_code', 'awaiting_target', "
        "'awaiting_unbind_confirmation')",
    )

    op.create_table(
        "channel_binding_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.String(length=512), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("handled", sa.Boolean(), nullable=False),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_events_channel",
        ),
        sa.UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            "event_id",
            name="uq_channel_binding_events_endpoint_event",
        ),
    )
    op.create_index(
        "ix_channel_binding_events_created_at",
        "channel_binding_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_channel_binding_events_created_at", table_name="channel_binding_events")
    op.drop_table("channel_binding_events")
    op.drop_constraint(
        "ck_channel_binding_flows_step",
        "channel_binding_flows",
        type_="check",
    )
    op.create_check_constraint(
        "ck_channel_binding_flows_step",
        "channel_binding_flows",
        "step IN ('awaiting_handle', 'awaiting_code', 'awaiting_target')",
    )
