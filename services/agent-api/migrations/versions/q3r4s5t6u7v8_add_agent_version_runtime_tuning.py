"""add runtime tuning fields to agent_versions

Revision ID: q3r4s5t6u7v8
Revises: p2q3r4s5t6u7
Create Date: 2026-08-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q3r4s5t6u7v8"
down_revision: str | Sequence[str] | None = "p2q3r4s5t6u7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TUNING_COLUMNS = (
    "memory_recall_top_k",
    "memory_recall_max_chars",
    "history_max_runs",
    "agent_max_requests_per_run",
)


def upgrade() -> None:
    for column in _TUNING_COLUMNS:
        op.add_column("agent_versions", sa.Column(column, sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in reversed(_TUNING_COLUMNS):
        op.drop_column("agent_versions", column)
