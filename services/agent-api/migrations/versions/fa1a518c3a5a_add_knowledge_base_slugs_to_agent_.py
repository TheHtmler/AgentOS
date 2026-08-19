"""add knowledge_base_slugs to agent_versions

Revision ID: fa1a518c3a5a
Revises: 3517361c3348
Create Date: 2026-08-19 12:17:25.503821

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# revision identifiers, used by Alembic.
revision: str = "fa1a518c3a5a"
down_revision: str | Sequence[str] | None = "3517361c3348"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stable id from scripts/seed_agents.py — the vertical (遗传代谢) agent is
# scoped to its one existing knowledge base; General stays NULL (unrestricted).
_IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def upgrade() -> None:
    """Add knowledge_base_slugs; backfill the vertical agent's published version.

    NULL means unrestricted (search every active KnowledgeBase) — General
    keeps this default. The vertical agent gets scoped to ``mma-pa`` so it
    doesn't silently gain access to future knowledge bases meant for other
    verticals; General does, by design, since it's meant to see everything.
    """

    op.add_column(
        "agent_versions",
        sa.Column("knowledge_base_slugs", sa.ARRAY(sa.Text()), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE agent_versions SET knowledge_base_slugs = :slugs "
            "WHERE agent_id = :agent_id AND is_published = true",
        ).bindparams(
            sa.bindparam("slugs", value=["mma-pa"], type_=sa.ARRAY(sa.Text())),
            sa.bindparam("agent_id", value=_IMD_AGENT_ID, type_=PG_UUID(as_uuid=True)),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "knowledge_base_slugs")
