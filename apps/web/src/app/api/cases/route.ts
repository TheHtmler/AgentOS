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

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const agentId = url.searchParams.get("agent_id");
    const query = agentId === null ? "" : `?agent_id=${encodeURIComponent(agentId)}`;
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/cases${query}`, {
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

export async function POST(request: Request) {
  try {
    const body = await request.text();
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/cases`, {
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
