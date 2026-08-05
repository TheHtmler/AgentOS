"""merge parenting/mma-pa into imd agent

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-08-05 20:05:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
MMA_PA_AGENT_ID = UUID("00000000-0000-0000-0000-000000000005")

IMD_OVERLAY = (
    "你是 AgentOS「遗传代谢」顾问：面向先天代谢异常（IMD）家庭的教育与随访助手，"
    "覆盖疾病公共知识、生长营养对照与日常管理问题（当前知识库以 MMA/PA 为主，可逐步扩展）。\n"
    "回答前先区分疾病/亚型标签（如 isolated_mma / cobalamin_disorder / pa 及基因标签），"
    "禁止把不同亚型结论默认同化。\n"
    "工具优先级：\n"
    "1) 疾病教育、急症识别、饮食/监测原则 → 优先 knowledge_search（可带 disease_tags），"
    "并引用 source_url；\n"
    "2) 身高/体重等生长对照 → 优先 growth_assess（WHO 2006）；缺性别、月龄或生日时只问一个问题；\n"
    "3) 库内不足或需要其他公开标准/页面时再用 web_search / fetch_url。\n"
    "不要让用户代查标准或粘贴曲线表；不要用长篇「我不是医生」替代作答。\n"
    "不给个体化处方剂量。区分已记录事实与推断。免责声明最多一句。"
    "仅在急性危险症状、擅自改饮食/药物、明显异常且资料不足、或需要个体化诊疗决策时，"
    "建议尽快就医。"
)


def upgrade() -> None:
    agents = sa.table(
        "agents",
        sa.column("id", sa.UUID()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("is_default", sa.Boolean()),
    )
    versions = sa.table(
        "agent_versions",
        sa.column("agent_id", sa.UUID()),
        sa.column("version", sa.Integer()),
        sa.column("system_prompt_overlay", sa.Text()),
        sa.column("memory_enabled", sa.Boolean()),
        sa.column("is_published", sa.Boolean()),
    )

    op.execute(
        agents.update()
        .where(agents.c.id == IMD_AGENT_ID)
        .values(
            slug="imd",
            name="遗传代谢",
            description=(
                "先天代谢异常（IMD）家庭助手：疾病教育、生长随访与日常管理"
                "（含 MMA/PA 等）。"
            ),
            status="active",
        ),
    )
    op.execute(
        versions.update()
        .where(
            versions.c.agent_id == IMD_AGENT_ID,
            versions.c.version == 1,
        )
        .values(
            system_prompt_overlay=IMD_OVERLAY,
            memory_enabled=True,
            is_published=True,
        ),
    )
    op.execute(
        agents.update()
        .where(sa.or_(agents.c.id == MMA_PA_AGENT_ID, agents.c.slug == "mma-pa"))
        .values(status="disabled", is_default=False),
    )


def downgrade() -> None:
    agents = sa.table(
        "agents",
        sa.column("id", sa.UUID()),
        sa.column("slug", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("is_default", sa.Boolean()),
    )
    versions = sa.table(
        "agent_versions",
        sa.column("agent_id", sa.UUID()),
        sa.column("version", sa.Integer()),
        sa.column("system_prompt_overlay", sa.Text()),
    )

    op.execute(
        agents.update()
        .where(agents.c.id == IMD_AGENT_ID)
        .values(
            slug="parenting",
            name="Parenting",
            description="A parenting guidance assistant.",
            status="active",
        ),
    )
    op.execute(
        versions.update()
        .where(
            versions.c.agent_id == IMD_AGENT_ID,
            versions.c.version == 1,
        )
        .values(
            system_prompt_overlay=(
                "你是 AgentOS 育儿顾问：帮助家长理解孩子档案、生长指标与常见养育问题。"
            ),
        ),
    )
    op.execute(
        agents.update()
        .where(agents.c.id == MMA_PA_AGENT_ID)
        .values(status="active"),
    )
