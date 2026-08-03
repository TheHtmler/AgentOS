export function formatMessageTimestamp(iso: string, now = new Date()): string {
  const date = new Date(iso);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const time = `${hours}:${minutes}`;

  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  if (sameDay) {
    return time;
  }

  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

export function formatRunDurationLabel(
  startedAt: string | null,
  completedAt: string | null,
  createdAt: string,
  status?: string,
): string | null {
  const startAt = startedAt ?? createdAt;

  if (status === "cancelled" || status === "failed") {
    return "已中断";
  }

  if (completedAt === null) {
    return null;
  }

  const milliseconds = new Date(completedAt).getTime() - new Date(startAt).getTime();

  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return null;
  }

  const seconds = milliseconds / 1_000;

  if (seconds < 60) {
    return `用时 ${seconds.toFixed(1)}s`;
  }

  const minutes = Math.floor(seconds / 60);
  const remain = Math.floor(seconds % 60);
  return `用时 ${minutes}m ${remain}s`;
}
