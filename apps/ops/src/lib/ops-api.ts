import { cookies } from "next/headers";

export const OPS_SESSION_COOKIE_NAME = "ops_session";

export function agentApiBaseUrl(): string {
  return (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");
}

export async function opsSessionHeaders(): Promise<HeadersInit> {
  const sessionToken = (await cookies()).get(OPS_SESSION_COOKIE_NAME)?.value;
  return sessionToken === undefined ? {} : { Cookie: `${OPS_SESSION_COOKIE_NAME}=${sessionToken}` };
}

export function upstreamResponseHeaders(upstream: Response): Headers {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const contentType = upstream.headers.get("content-type");
  if (contentType !== null) {
    headers.set("Content-Type", contentType);
  }
  return headers;
}

export function proxyUpstreamResponse(upstream: Response): Response {
  if (upstream.status === 204 || upstream.status === 205 || upstream.status === 304) {
    return new Response(null, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstreamResponseHeaders(upstream),
  });
}
