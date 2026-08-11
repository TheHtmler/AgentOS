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
  params: Promise<{ caseId: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  try {
    const { caseId } = await context.params;
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/cases/${caseId}/members`, {
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}

export async function POST(request: Request, context: RouteContext) {
  try {
    const { caseId } = await context.params;
    const body = await request.text();
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/cases/${caseId}/members`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(await agentApiSessionHeaders()),
      },
      body,
      cache: "no-store",
      signal: request.signal,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}
