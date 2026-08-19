"""add optional Responses reasoning summary configuration

Revision ID: n0o1p2q3r4s5
Revises: m9n0o1p2q3r4
Create Date: 2026-08-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n0o1p2q3r4s5"
down_revision: str | None = "m9n0o1p2q3r4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column("reasoning_summary", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_model_providers_reasoning_summary",
        "model_providers",
        "reasoning_summary IS NULL OR reasoning_summary IN ('auto', 'concise', 'detailed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_model_providers_reasoning_summary",
        "model_providers",
        type_="check",
    )
    op.drop_column("model_providers", "reasoning_summary")
