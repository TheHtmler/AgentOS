"""expand Case membership roles

Revision ID: j6k7l8m9n0
Revises: i5j6k7l8m9n0
Create Date: 2026-08-11 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j6k7l8m9n0"
down_revision: str | Sequence[str] | None = "i5j6k7l8m9n0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_case_memberships_role",
        "case_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_case_memberships_role",
        "case_memberships",
        "role IN ('owner', 'editor', 'viewer')",
    )


def downgrade() -> None:
    has_non_owner = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM case_memberships WHERE role <> 'owner')",
            ),
        )
        .scalar()
    )
    if has_non_owner:
        raise RuntimeError("Cannot downgrade Case roles while editor/viewer memberships exist")
    op.drop_constraint(
        "ck_case_memberships_role",
        "case_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_case_memberships_role",
        "case_memberships",
        "role IN ('owner')",
    )
