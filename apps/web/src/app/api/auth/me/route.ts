import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/auth/me`, {
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return Response.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
