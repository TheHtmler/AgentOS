import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/me/memories`, {
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return new Response(null, { status: 503 });
  }
}
