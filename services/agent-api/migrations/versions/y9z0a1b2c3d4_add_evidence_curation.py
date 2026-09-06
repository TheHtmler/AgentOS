"""add pending evidence curation metadata

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-09-06 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "y9z0a1b2c3d4"
down_revision: str | Sequence[str] | None = "x8y9z0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_knowledge_documents_source_kind", "knowledge_documents", type_="check")
    op.create_check_constraint(
        "ck_knowledge_documents_source_kind",
        "knowledge_documents",
        "source_kind IN ("
        "'official_reference', 'clinical_guideline', 'curated_summary', 'research_article'"
        ")",
    )
    op.drop_constraint("ck_knowledge_documents_review_status", "knowledge_documents", type_="check")
    op.create_check_constraint(
        "ck_knowledge_documents_review_status",
        "knowledge_documents",
        "review_status IN ('pending_review', 'curated', 'clinically_reviewed', 'withdrawn')",
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "ontology_terms",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "ontology_terms")
    op.drop_constraint("ck_knowledge_documents_review_status", "knowledge_documents", type_="check")
    op.create_check_constraint(
        "ck_knowledge_documents_review_status",
        "knowledge_documents",
        "review_status IN ('curated', 'clinically_reviewed', 'withdrawn')",
    )
    op.drop_constraint("ck_knowledge_documents_source_kind", "knowledge_documents", type_="check")
    op.create_check_constraint(
        "ck_knowledge_documents_source_kind",
        "knowledge_documents",
        "source_kind IN ('official_reference', 'clinical_guideline', 'curated_summary')",
    )
