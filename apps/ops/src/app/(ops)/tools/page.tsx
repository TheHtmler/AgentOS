import { ToolsInventory } from "@/components/tools-inventory";

export default function ToolsPage() {
  return (
    <ToolsInventory
      title="工具"
      lead="运行时可注册工具总目录：查看输入/输出 Schema、来源、风险级别和当前策略。"
      source="all"
    />
  );
}
