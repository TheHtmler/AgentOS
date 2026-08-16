import { NextResponse } from "next/server";

import { agentApiBaseUrl, upstreamResponseHeaders } from "@/lib/agent-api";

type InspectInvitationRequest = {
  token: string;
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function isInspectInvitationRequest(value: unknown): value is InspectInvitationRequest {
  return (
    typeof value === "object" &&
    value !== null &&
    "token" in value &&
    typeof value.token === "string" &&
    value.token.length > 0 &&
    value.token.length <= 512
  );
}

export async function POST(request: Request) {
  let payload: unknown;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  if (!isInspectInvitationRequest(payload)) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/auth/invitations/inspect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: payload.token }),
      cache: "no-store",
      signal: request.signal,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
