import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ threadId: string; runId: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  const { threadId, runId } = await context.params;
  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/sessions/${threadId}/runs/${runId}/events`,
      {
        headers: await opsSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
