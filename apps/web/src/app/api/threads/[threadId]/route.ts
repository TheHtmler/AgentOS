import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ threadId: string }>;
};

function isUuid(value: string): boolean {
  const parts = value.split("-");

  return (
    parts.length === 5 &&
    parts[0].length === 8 &&
    parts[1].length === 4 &&
    parts[2].length === 4 &&
    parts[3].length === 4 &&
    parts[4].length === 12 &&
    parts.every((part) => /^[0-9a-f]+$/i.test(part))
  );
}

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_thread_id" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function PATCH(request: Request, context: RouteContext) {
  const { threadId } = await context.params;

  if (!isUuid(threadId)) {
    return invalidRequestResponse();
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/threads/${threadId}`, {
      method: "PATCH",
      headers: {
        ...(await agentApiSessionHeaders()),
        "Content-Type": "application/json",
      },
      body: await request.text(),
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

export async function DELETE(request: Request, context: RouteContext) {
  const { threadId } = await context.params;

  if (!isUuid(threadId)) {
    return invalidRequestResponse();
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/threads/${threadId}`, {
      method: "DELETE",
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
