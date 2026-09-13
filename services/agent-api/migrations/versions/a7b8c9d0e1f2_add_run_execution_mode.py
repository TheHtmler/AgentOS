"""Add execution mode to runs.

Revision ID: a7b8c9d0e1f2
Revises: z0a1b2c3d4e5
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("execution_mode", sa.String(length=16), nullable=True))
    op.execute("UPDATE runs SET execution_mode = 'normal' WHERE execution_mode IS NULL")
    op.alter_column("runs", "execution_mode", nullable=False, server_default="normal")


def downgrade() -> None:
    op.drop_column("runs", "execution_mode")
