/** Display labels for ops enums (API values stay English). */

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  curated: "已策展",
  clinically_reviewed: "临床已审",
  withdrawn: "已撤回",
};

export const SOURCE_KIND_LABELS: Record<string, string> = {
  official_reference: "官方参考",
  clinical_guideline: "临床指南",
  curated_summary: "策展摘要",
};

export const AGENT_STATUS_LABELS: Record<string, string> = {
  active: "启用中",
  disabled: "已禁用",
};

export const AGENT_KIND_LABELS: Record<string, string> = {
  general: "通用",
  vertical: "垂类",
};

export function labelOf(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) return "—";
  return map[value] ?? value;
}

export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  waiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const USER_STATUS_LABELS: Record<string, string> = {
  invited: "已邀请",
  active: "活跃",
  disabled: "已禁用",
};

export const TOOL_DOMAIN_LABELS: Record<string, string> = {
  search: "搜索",
  fetch: "抓取",
  growth: "生长评估",
  util: "计算",
  knowledge: "知识库",
  case: "档案",
  artifact: "产物",
  mcp: "MCP",
};

export const TOOL_RISK_LABELS: Record<string, string> = {
  read: "只读",
  write: "写入",
  exec: "执行",
  external: "外部",
};

export const POLICY_ACTION_LABELS: Record<string, string> = {
  allow: "允许",
  ask: "需审批",
  deny: "拒绝",
};

export const MESSAGE_ROLE_LABELS: Record<string, string> = {
  user: "用户",
  assistant: "助手",
  system: "系统",
  tool: "工具",
};

export function boolZh(value: boolean | null | undefined): string {
  if (value == null) return "—";
  return value ? "开" : "关";
}
