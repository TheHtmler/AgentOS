"""remove local model provider support

Revision ID: x8y9z0a1b2c3
Revises: w8x9y0z1a2b
Create Date: 2026-09-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "x8y9z0a1b2c3"
down_revision: str | Sequence[str] | None = "w8x9y0z1a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete legacy local bindings and simplify the third-party Provider schema."""

    provider_columns = sa.inspect(op.get_bind()).get_columns("model_providers")
    columns = {column["name"] for column in provider_columns}
    op.execute("DELETE FROM agent_versions WHERE model_provider_id IS NULL")
    op.alter_column("agent_versions", "model_provider_id", nullable=False)
    if "kind" in columns:
        op.execute("DELETE FROM model_providers WHERE kind = 'local'")
        op.drop_constraint("ck_model_providers_kind", "model_providers", type_="check")
        op.drop_column("model_providers", "kind")
    if "is_builtin" in columns:
        op.drop_column("model_providers", "is_builtin")


def downgrade() -> None:
    op.add_column(
        "model_providers",
        sa.Column("kind", sa.String(16), server_default=sa.text("'remote'"), nullable=False),
    )
    op.add_column(
        "model_providers",
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        "ck_model_providers_kind",
        "model_providers",
        "kind IN ('local', 'remote')",
    )
    op.alter_column("agent_versions", "model_provider_id", nullable=True)
