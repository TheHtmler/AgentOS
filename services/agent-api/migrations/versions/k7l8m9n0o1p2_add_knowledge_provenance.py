"""add knowledge source provenance and review metadata

Revision ID: k7l8m9n0o1p2
Revises: j6k7l8m9n0
Create Date: 2026-08-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k7l8m9n0o1p2"
down_revision: str | Sequence[str] | None = "j6k7l8m9n0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "source_kind",
            sa.String(length=32),
            server_default=sa.text("'curated_summary'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("source_date", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "review_status",
            sa.String(length=24),
            server_default=sa.text("'curated'"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_knowledge_documents_source_kind",
        "knowledge_documents",
        "source_kind IN ('official_reference', 'clinical_guideline', 'curated_summary')",
    )
    op.create_check_constraint(
        "ck_knowledge_documents_review_status",
        "knowledge_documents",
        "review_status IN ('curated', 'clinically_reviewed', 'withdrawn')",
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("section_label", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "section_label")
    op.drop_constraint(
        "ck_knowledge_documents_review_status",
        "knowledge_documents",
        type_="check",
    )
    op.drop_constraint(
        "ck_knowledge_documents_source_kind",
        "knowledge_documents",
        type_="check",
    )
    op.drop_column("knowledge_documents", "reviewed_at")
    op.drop_column("knowledge_documents", "review_status")
    op.drop_column("knowledge_documents", "source_date")
    op.drop_column("knowledge_documents", "source_kind")
