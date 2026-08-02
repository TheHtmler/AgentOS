import { cookies } from "next/headers";

export const SESSION_COOKIE_NAME = "agentos_session";

export function agentApiBaseUrl(): string {
  return (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

export async function agentApiSessionHeaders(): Promise<HeadersInit> {
  const sessionToken = (await cookies()).get(SESSION_COOKIE_NAME)?.value;

  // The browser never sees the Agent API origin; only its HttpOnly cookie reaches this BFF.
  return sessionToken === undefined ? {} : { Cookie: `${SESSION_COOKIE_NAME}=${sessionToken}` };
}

export function upstreamResponseHeaders(upstream: Response): Headers {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const contentType = upstream.headers.get("content-type");

  if (contentType !== null) {
    headers.set("Content-Type", contentType);
  }

  return headers;
}
