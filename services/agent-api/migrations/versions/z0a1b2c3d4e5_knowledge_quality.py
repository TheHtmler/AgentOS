"""Track import quality and embedding provenance without rewriting existing content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("import_stage", sa.String(32)))
    for name in ("ingestion", "import_details"):
        op.add_column(
            "knowledge_documents",
            sa.Column(name, JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        )
    op.add_column("knowledge_chunks", sa.Column("embedding_version", sa.String(32)))


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding_version")
    for name in ("import_details", "ingestion", "import_stage"):
        op.drop_column("knowledge_documents", name)
