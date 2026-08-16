import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ documentId: string; snapshotId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  const { documentId, snapshotId } = await context.params;
  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/knowledge/documents/${documentId}/snapshots/${snapshotId}/restore`,
      {
        method: "POST",
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
