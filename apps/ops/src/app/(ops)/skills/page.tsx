import { ToolsInventory } from "@/components/tools-inventory";

export default function SkillsPage() {
  return (
    <ToolsInventory
      title="技能"
      lead="运行时内建工具清单：能力域、风险级别、默认策略与当前开关。"
      source="builtin"
    />
  );
}
