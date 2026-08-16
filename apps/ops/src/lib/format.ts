export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function displayTitle(title: string | null | undefined, fallback = "未命名会话"): string {
  const trimmed = title?.trim();
  return trimmed ? trimmed : fallback;
}
