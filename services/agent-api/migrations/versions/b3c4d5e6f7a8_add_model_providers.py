"""add model_providers and agent_versions.model_provider_id

Revision ID: b3c4d5e6f7a8
Revises: fa1a518c3a5a
Create Date: 2026-08-19 16:00:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "fa1a518c3a5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Stable id shared with db/provider_store.py — the env-managed local Ollama
# endpoint. Values below mirror config.py defaults; the lifespan startup sync
# rewrites them from the actual env settings on every boot.
BUILTIN_LOCAL_PROVIDER_ID = UUID("00000000-0000-0000-0000-000000000010")


def upgrade() -> None:
    """Create model_providers and let Agent versions pin a provider.

    Existing versions keep NULL model_provider_id, which resolves to the
    built-in local provider — exactly the model they historically ran on.
    """

    op.create_table(
        "model_providers",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("default_model", sa.String(128), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False),
        sa.Column(
            "supports_vision",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("is_builtin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_model_providers_slug"),
        sa.CheckConstraint("kind IN ('local', 'remote')", name="ck_model_providers_kind"),
    )
    op.execute(
        sa.text(
            "INSERT INTO model_providers ("
            "id, slug, name, kind, base_url, api_key, default_model,"
            " context_window, max_output_tokens, temperature, max_concurrent_runs,"
            " supports_vision, enabled, is_builtin"
            ") VALUES ("
            ":id, 'local', 'Local Ollama', 'local', :base_url, NULL, :model,"
            " :context_window, :max_output_tokens, NULL, 1, true, true, true"
            ")"
        ).bindparams(
            sa.bindparam("id", value=BUILTIN_LOCAL_PROVIDER_ID, type_=PG_UUID(as_uuid=True)),
            sa.bindparam("base_url", value="http://127.0.0.1:11434/v1"),
            sa.bindparam("model", value="agentos-qwen3vl:16k"),
            sa.bindparam("context_window", value=16_384),
            sa.bindparam("max_output_tokens", value=4_096),
        ),
    )
    op.add_column(
        "agent_versions",
        sa.Column("model_provider_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_agent_versions_model_provider_id",
        "agent_versions",
        ["model_provider_id"],
    )
    op.create_foreign_key(
        "fk_agent_versions_model_provider_id",
        "agent_versions",
        "model_providers",
        ["model_provider_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_versions_model_provider_id",
        "agent_versions",
        type_="foreignkey",
    )
    op.drop_index("ix_agent_versions_model_provider_id", table_name="agent_versions")
    op.drop_column("agent_versions", "model_provider_id")
    op.drop_table("model_providers")
