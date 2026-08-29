import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

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
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/channel-bindings`, {
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return unavailableResponse();
  }
}

export async function POST(request: Request) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/channel-bindings/pairing-codes`, {
      method: "POST",
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return unavailableResponse();
  }
}
