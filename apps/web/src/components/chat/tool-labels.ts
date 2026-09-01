const TOOL_LABELS: Record<string, string> = {
  web_search: "搜索公开资料",
  fetch_url: "读取网页",
  read_artifact: "读取附件",
  calculate: "计算结果",
  time_diff: "计算时间差",
  growth_assess: "分析成长数据",
  knowledge_search: "查询知识库",
  sandbox_exec: "执行工作区命令",
  case_slot_collect: "补充当前资料",
  case_attribution_confirm: "确认当前资料",
};

export function toolDisplayName(toolName: string): string {
  if (TOOL_LABELS[toolName]) {
    return TOOL_LABELS[toolName];
  }

  if (toolName.startsWith("mcp_")) {
    return "连接外部服务";
  }

  if (toolName.startsWith("case_")) {
    return "更新当前资料";
  }

  return "处理任务";
}

export function toolProgressLabel(toolName: string, status: "running" | "done" | "error") {
  const label = toolDisplayName(toolName);

  if (status === "running") {
    return `${label}中…`;
  }
  if (status === "error") {
    return `${label}失败`;
  }
  return `${label}完成`;
}
