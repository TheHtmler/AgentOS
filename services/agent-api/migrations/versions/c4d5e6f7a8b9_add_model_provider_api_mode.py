"""add api_mode to model_providers

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-19 19:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Codex-class gateways only serve /responses; chat_completions stays default."""

    op.add_column(
        "model_providers",
        sa.Column(
            "api_mode",
            sa.String(24),
            server_default=sa.text("'chat_completions'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_model_providers_api_mode",
        "model_providers",
        "api_mode IN ('chat_completions', 'responses')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_model_providers_api_mode", "model_providers", type_="check")
    op.drop_column("model_providers", "api_mode")
