import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function DELETE(request: Request, { params }: { params: Promise<{ artifactId: string }> }) {
  const { artifactId } = await params;
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/me/artifacts/${artifactId}`, {
      method: "DELETE", headers: await agentApiSessionHeaders(), cache: "no-store", signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch { return new Response(null, { status: 503 }); }
}
