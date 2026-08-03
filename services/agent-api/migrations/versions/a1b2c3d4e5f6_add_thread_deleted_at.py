"""add threads.deleted_at for soft delete"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "de41fa14ada9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_threads_deleted_at", "threads", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_threads_deleted_at", table_name="threads")
    op.drop_column("threads", "deleted_at")
