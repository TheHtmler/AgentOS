"""add thread owners

Revision ID: 4edcb11d60ef
Revises: 96fdda91fdad
Create Date: 2026-08-02 18:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4edcb11d60ef"
down_revision: str | Sequence[str] | None = "96fdda91fdad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing local-development Threads have no trustworthy identity to backfill. Keeping them
    # unowned makes them inaccessible instead of accidentally assigning another user's history.
    op.add_column("threads", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_threads_user_id_users",
        "threads",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_threads_user_id"), "threads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_threads_user_id"), table_name="threads")
    op.drop_constraint("fk_threads_user_id_users", "threads", type_="foreignkey")
    op.drop_column("threads", "user_id")
