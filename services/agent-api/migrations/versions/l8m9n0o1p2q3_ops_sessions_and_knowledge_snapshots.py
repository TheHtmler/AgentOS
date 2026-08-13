"""add ops_sessions and knowledge_document_snapshots

Revision ID: l8m9n0o1p2q3
Revises: k7l8m9n0o1p2
Create Date: 2026-08-13 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "l8m9n0o1p2q3"
down_revision: str | Sequence[str] | None = "k7l8m9n0o1p2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("token_hash", name="uq_ops_sessions_token_hash"),
    )
    op.create_index("ix_ops_sessions_subject", "ops_sessions", ["subject"])

    op.create_table(
        "knowledge_document_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_label", sa.String(length=128)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_knowledge_document_snapshots_document_id",
        "knowledge_document_snapshots",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_document_snapshots_document_id",
        table_name="knowledge_document_snapshots",
    )
    op.drop_table("knowledge_document_snapshots")
    op.drop_index("ix_ops_sessions_subject", table_name="ops_sessions")
    op.drop_table("ops_sessions")
