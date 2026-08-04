import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ runId: string }>;
};

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_run_id" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: Request, context: RouteContext) {
  const { runId } = await context.params;

  if (!isUuid(runId)) {
    return invalidRequestResponse();
  }

  try {
    const body = await request.text();
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/runs/${runId}/resume`, {
      method: "POST",
      headers: {
        ...(await agentApiSessionHeaders()),
        "Content-Type": "application/json",
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
