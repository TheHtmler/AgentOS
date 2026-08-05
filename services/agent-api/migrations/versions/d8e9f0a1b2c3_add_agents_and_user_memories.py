"""add agents and user memories

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-05 17:50:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENERAL_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
PARENTING_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
GENERAL_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000003")
PARENTING_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000004")


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('general', 'vertical')", name="ck_agents_kind"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_agents_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(
        "uq_agents_one_default",
        "agents",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("system_prompt_overlay", sa.Text(), nullable=False),
        sa.Column(
            "tool_policy_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_published",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_table(
        "user_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_user_memories_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_thread_id"],
            ["threads.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"])
    op.create_index("ix_user_memories_agent_id", "user_memories", ["agent_id"])
    op.create_index(
        "ix_user_memories_user_agent_status",
        "user_memories",
        ["user_id", "agent_id", "status"],
    )
    op.create_index(
        "ix_user_memories_tags_gin",
        "user_memories",
        ["tags"],
        postgresql_using="gin",
    )

    agents = sa.table(
        "agents",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("kind", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("status", sa.String()),
    )
    agent_versions = sa.table(
        "agent_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("agent_id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("system_prompt_overlay", sa.Text()),
        sa.column("memory_enabled", sa.Boolean()),
        sa.column("is_published", sa.Boolean()),
    )
    op.bulk_insert(
        agents,
        [
            {
                "id": GENERAL_AGENT_ID,
                "slug": "general",
                "name": "General",
                "description": "Default general-purpose AgentOS assistant.",
                "kind": "general",
                "is_default": True,
                "status": "active",
            },
            {
                "id": PARENTING_AGENT_ID,
                "slug": "parenting",
                "name": "Parenting",
                "description": "A parenting guidance assistant.",
                "kind": "vertical",
                "is_default": False,
                "status": "active",
            },
        ],
    )
    op.bulk_insert(
        agent_versions,
        [
            {
                "id": GENERAL_AGENT_VERSION_ID,
                "agent_id": GENERAL_AGENT_ID,
                "version": 1,
                "system_prompt_overlay": "",
                "memory_enabled": False,
                "is_published": True,
            },
            {
                "id": PARENTING_AGENT_VERSION_ID,
                "agent_id": PARENTING_AGENT_ID,
                "version": 1,
                "system_prompt_overlay": (
                    "你是 AgentOS 育儿顾问：帮助家长理解孩子档案、生长指标与常见养育问题。\n"
                    "当用户给出身高/体重/头围等测量并要求对照标准曲线或生长情况时：\n"
                    "1) 先用工具检索权威公开标准（如 WHO 儿童生长标准、中国卫健委相关标准），"
                    "必要时 fetch_url 打开具体页面；\n"
                    "2) 用检索到的标准对照用户数据，给出区间/百分位/趋势解读，并附上来源 URL；\n"
                    "3) 缺性别、月龄/生日等无法对照的关键字段时，只问一个问题；\n"
                    "4) 不要让用户自己去找或粘贴标准曲线表；不要用长篇「我不是医生」替代作答。\n"
                    "区分已记录事实与推断。免责声明最多一句。仅在急性危险症状、明显异常且资料不足、"
                    "或需要个体化诊疗决策时，建议尽快就医。"
                ),
                "memory_enabled": True,
                "is_published": True,
            },
        ],
    )

    # Add nullable first so the seeded default can safely backfill every pre-existing thread.
    op.add_column(
        "threads",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_threads_agent_id_agents",
        "threads",
        "agents",
        ["agent_id"],
        ["id"],
    )
    op.create_index("ix_threads_agent_id", "threads", ["agent_id"])
    op.execute(
        sa.text(
            "UPDATE threads SET agent_id = :general_agent_id WHERE agent_id IS NULL",
        ).bindparams(
            general_agent_id=GENERAL_AGENT_ID,
        ),
    )
    op.alter_column("threads", "agent_id", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_threads_agent_id", table_name="threads")
    op.drop_constraint("fk_threads_agent_id_agents", "threads", type_="foreignkey")
    op.drop_column("threads", "agent_id")

    op.drop_index("ix_user_memories_tags_gin", table_name="user_memories")
    op.drop_index("ix_user_memories_user_agent_status", table_name="user_memories")
    op.drop_index("ix_user_memories_agent_id", table_name="user_memories")
    op.drop_index("ix_user_memories_user_id", table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_index("ix_agent_versions_agent_id", table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_index("uq_agents_one_default", table_name="agents")
    op.drop_table("agents")
