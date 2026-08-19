"""enforce one published version per agent

Revision ID: m9n0o1p2q3r4
Revises: c4d5e6f7a8b9, l8m9n0o1p2q3
Create Date: 2026-08-19 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m9n0o1p2q3r4"
down_revision: str | Sequence[str] | None = ("c4d5e6f7a8b9", "l8m9n0o1p2q3")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the newest published revision before enforcing the invariant."""

    op.execute(
        sa.text(
            "UPDATE agent_versions AS older "
            "SET is_published = false "
            "WHERE older.is_published = true "
            "AND EXISTS ("
            "SELECT 1 FROM agent_versions AS newer "
            "WHERE newer.agent_id = older.agent_id "
            "AND newer.is_published = true "
            "AND newer.version > older.version"
            ")"
        )
    )
    op.create_index(
        "uq_agent_versions_one_published",
        "agent_versions",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text("is_published = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_versions_one_published", table_name="agent_versions")
