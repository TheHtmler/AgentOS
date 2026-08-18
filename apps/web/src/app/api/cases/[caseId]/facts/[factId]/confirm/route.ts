import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

type RouteContext = {
  params: Promise<{ caseId: string; factId: string }>;
};

export async function POST(request: Request, context: RouteContext) {
  try {
    const { caseId, factId } = await context.params;
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/cases/${caseId}/facts/${factId}/confirm`,
      {
        method: "POST",
        headers: await agentApiSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}
