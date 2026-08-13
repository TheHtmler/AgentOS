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

export function boolZh(value: boolean | null | undefined): string {
  if (value == null) return "—";
  return value ? "开" : "关";
}
