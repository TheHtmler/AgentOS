import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ artifactId: string }> };

export async function GET(_request: Request, context: RouteContext) {
  const { artifactId } = await context.params;
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(artifactId)) {
    return NextResponse.json({ error: "invalid_artifact_id" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/uploads/${artifactId}/thumbnail`, {
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return new Response(null, { status: 503 });
  }
}
