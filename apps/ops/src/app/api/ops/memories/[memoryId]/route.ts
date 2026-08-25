import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ memoryId: string }>;
};

export async function DELETE(request: Request, context: RouteContext) {
  const { memoryId } = await context.params;
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/memories/${memoryId}`, {
      method: "DELETE",
      headers: await opsSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
