export class OpsFetchError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Surface backend error detail in actionable Chinese wherever possible. */

export function errorMessage(
  body: { detail?: string; error?: string } | null,
  status: number,
): string {
  if (body?.detail) return body.detail;
  if (body?.error === "agent_api_unavailable") {
    return "后端服务不可用：无法连接 agent-api，请确认服务已启动（或部署后已就绪）";
  }
  if (body?.error) return body.error;
  return `请求失败（${status}）`;
}

export async function opsJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
      error?: string;
    } | null;
    throw new OpsFetchError(response.status, errorMessage(body, response.status));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
