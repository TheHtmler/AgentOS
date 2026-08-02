"""add password hash to users"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "de41fa14ada9"
down_revision: str | Sequence[str] | None = "4edcb11d60ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable to support accounts activated by the previous invite-only flow.
    # The new registration flow always writes a password hash for new active users.
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_set_at")
    op.drop_column("users", "password_hash")