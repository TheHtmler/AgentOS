"""add knowledge tables

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-05 19:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_knowledge_bases_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "knowledge_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_label", sa.String(length=256), nullable=True),
        sa.Column("version_label", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "slug",
            name="uq_knowledge_documents_base_slug",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_knowledge_base_id",
        "knowledge_documents",
        ["knowledge_base_id"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_document_id",
        "knowledge_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_knowledge_chunks_tags",
        "knowledge_chunks",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_tags", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_document_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_knowledge_documents_knowledge_base_id",
        table_name="knowledge_documents",
    )
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_bases")
