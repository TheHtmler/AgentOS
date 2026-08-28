"""add import status to knowledge_documents

Revision ID: s5t6u7v8w9x0
Revises: r4s5t6u7v8
Create Date: 2026-08-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s5t6u7v8w9x0"
down_revision: str | Sequence[str] | None = "r4s5t6u7v8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "import_status",
            sa.String(length=16),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("import_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("import_progress_done", sa.Integer(), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("import_progress_total", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_knowledge_documents_import_status",
        "knowledge_documents",
        "import_status IN ('ready', 'processing', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_knowledge_documents_import_status",
        "knowledge_documents",
        type_="check",
    )
    op.drop_column("knowledge_documents", "import_progress_total")
    op.drop_column("knowledge_documents", "import_progress_done")
    op.drop_column("knowledge_documents", "import_error")
    op.drop_column("knowledge_documents", "import_status")
