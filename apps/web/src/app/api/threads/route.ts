import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function invalidLimitResponse() {
  return NextResponse.json(
    { error: "invalid_limit" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

function requestedLimit(request: Request): number | null {
  const value = new URL(request.url).searchParams.get("limit");

  if (value === null) {
    return 20;
  }

  if (!/^(?:[1-9]|[1-4][0-9]|50)$/.test(value)) {
    return null;
  }

  return Number(value);
}

export async function GET(request: Request) {
  const limit = requestedLimit(request);

  if (limit === null) {
    return invalidLimitResponse();
  }

  try {
    const query = new URLSearchParams({ limit: String(limit) });
    const agentId = new URL(request.url).searchParams.get("agent_id");
    if (agentId !== null) {
      query.set("agent_id", agentId);
    }
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/threads?${query}`, {
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
