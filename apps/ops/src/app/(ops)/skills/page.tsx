import { ToolsInventory } from "@/components/tools-inventory";

export default function SkillsPage() {
  return (
    <ToolsInventory
      title="技能"
      lead="运行时内建工具清单：能力域、风险级别、策略与当前开关。点击工具查看输入/输出 Schema。"
      source="builtin"
    />
  );
}
