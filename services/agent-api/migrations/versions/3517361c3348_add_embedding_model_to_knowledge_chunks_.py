"""add embedding_model to knowledge_chunks and user_memories

Revision ID: 3517361c3348
Revises: l8m9n0o1p2q3
Create Date: 2026-08-17 19:58:59.874666

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3517361c3348"
down_revision: str | Sequence[str] | None = "l8m9n0o1p2q3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_HISTORICAL_EMBEDDING_MODEL = "nomic-embed-text"


def upgrade() -> None:
    """Add embedding_model, backfilled for existing rows.

    ``memory_embedding_model`` has never had another value in this codebase,
    so backfilling existing embedded rows with it is stating a known fact,
    not a guess — it avoids every pre-existing embedding losing its vector
    score on the day this ships. Rows embedded after a future model change
    will carry the new model name from the write path and be compared
    against it going forward.
    """

    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "user_memories",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )
    for table in ("knowledge_chunks", "user_memories"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET embedding_model = :model "  # noqa: S608
                "WHERE embedding IS NOT NULL AND embedding_model IS NULL",
            ).bindparams(model=_HISTORICAL_EMBEDDING_MODEL),
        )


def downgrade() -> None:
    op.drop_column("user_memories", "embedding_model")
    op.drop_column("knowledge_chunks", "embedding_model")
