"""add supports_tools capability flag to model_providers

Revision ID: o1p2q3r4s5t6
Revises: n0o1p2q3r4s5
Create Date: 2026-08-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o1p2q3r4s5t6"
down_revision: str | None = "n0o1p2q3r4s5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column(
            "supports_tools",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("model_providers", "supports_tools")
