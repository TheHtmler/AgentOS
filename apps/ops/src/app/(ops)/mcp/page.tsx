import { ToolsInventory } from "@/components/tools-inventory";

export default function McpPage() {
  return (
    <ToolsInventory
      title="MCP"
      lead="已登记的外部 MCP 工具。当前产品未配置 MCP 服务器，因此清单通常为空。"
      source="mcp"
    />
  );
}
