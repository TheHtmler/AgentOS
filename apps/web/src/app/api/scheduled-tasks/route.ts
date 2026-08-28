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
  const limit = new URL(request.url).searchParams.get("limit") ?? "20";

  if (!/^(?:[1-9]|[1-4][0-9]|50)$/.test(limit)) {
    return NextResponse.json(
      { error: "invalid_limit" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/scheduled-tasks?limit=${limit}`, {
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
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/scheduled-tasks`, {
      method: "POST",
      headers: {
        ...(await agentApiSessionHeaders()),
        "Content-Type": "application/json",
      },
      body: await request.text(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return unavailableResponse();
  }
}
