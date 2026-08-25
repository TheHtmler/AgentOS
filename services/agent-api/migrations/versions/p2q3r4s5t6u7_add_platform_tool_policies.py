"""add platform_tool_policies

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p2q3r4s5t6u7"
down_revision: str | Sequence[str] | None = "o1p2q3r4s5t6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_tool_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('ask', 'deny')",
            name="ck_platform_tool_policies_action",
        ),
        sa.UniqueConstraint("tool_name", name="uq_platform_tool_policies_tool_name"),
    )


def downgrade() -> None:
    op.drop_table("platform_tool_policies")
