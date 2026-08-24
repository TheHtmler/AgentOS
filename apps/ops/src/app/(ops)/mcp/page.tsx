import { ToolsInventory } from "@/components/tools-inventory";

export default function McpPage() {
  return (
    <ToolsInventory
      title="MCP 接入"
      lead="外部 MCP 工具来源清单。工具详情统一在工具目录查看。"
      source="mcp"
    />
  );
}
