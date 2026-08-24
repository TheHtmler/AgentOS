import { ToolsInventory } from "@/components/tools-inventory";

export default function McpPage() {
  return (
    <ToolsInventory
      title="MCP"
      lead="已登记的外部 MCP 工具。点击工具查看运行时提供的输入/输出定义。"
      source="mcp"
    />
  );
}
