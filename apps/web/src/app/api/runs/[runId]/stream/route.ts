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

// Subscribe to the live event stream of an in-flight resumed run. 204 (and
// any non-OK status) tells the client to fall back to polling + history reload.
export async function GET(request: Request, context: RouteContext) {
  const { runId } = await context.params;

  if (!isUuid(runId)) {
    return invalidRequestResponse();
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/runs/${runId}/stream`, {
      method: "GET",
      headers: {
        ...(await agentApiSessionHeaders()),
      },
      cache: "no-store",
      signal: request.signal,
    });

    if (upstream.status === 204) {
      return new Response(null, {
        status: 204,
        headers: { "Cache-Control": "no-store" },
      });
    }

    const headers = upstreamResponseHeaders(upstream);
    if (upstream.ok) {
      headers.set("Cache-Control", "no-cache, no-transform");
      headers.set("X-Accel-Buffering", "no");
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers,
    });
  } catch {
    return unavailableResponse();
  }
}
