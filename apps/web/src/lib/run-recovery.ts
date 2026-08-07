/** Run statuses where the server is still working after the browser SSE drops. */
export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "waiting_approval"]);

export function isActiveRunStatus(status: string | null | undefined): boolean {
  return typeof status === "string" && ACTIVE_RUN_STATUSES.has(status);
}

/**
 * Heuristic for SSE/fetch drops (mobile background, tab freeze, flaky network).
 * When unsure, callers should still probe `/api/runs/{id}` before showing a hard error.
 */
export function isLikelyTransportDisconnect(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return true;
  }

  const message = error.message.toLowerCase();
  if (message.includes("409")) {
    return false;
  }

  const needles = [
    "failed to fetch",
    "networkerror",
    "network request failed",
    "load failed",
    "aborted",
    "the user aborted",
    "bodystream",
    "body stream",
    "connection",
    "econnreset",
    "socket",
    "timeout",
    "premature close",
    "stream ended",
    "err_network",
    "err_internet_disconnected",
  ];

  return needles.some((needle) => message.includes(needle));
}
