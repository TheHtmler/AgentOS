import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ memoryId: string }> },
) {
  const { memoryId } = await params;
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/me/memories/${memoryId}`, {
      method: "DELETE",
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return new Response(null, { status: 503 });
  }
}
