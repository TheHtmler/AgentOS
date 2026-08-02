import { NextResponse } from "next/server";

import { agentApiBaseUrl, SESSION_COOKIE_NAME, upstreamResponseHeaders } from "@/lib/agent-api";

type VerifyRequest = {
  token: string;
};

type VerifyResponse = {
  user_id: string;
  email: string;
  session_token: string;
  expires_at: string;
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function isVerifyRequest(value: unknown): value is VerifyRequest {
  return (
    typeof value === "object" &&
    value !== null &&
    "token" in value &&
    typeof value.token === "string" &&
    value.token.length > 0 &&
    value.token.length <= 512
  );
}

function isVerifyResponse(value: unknown): value is VerifyResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "user_id" in value &&
    typeof value.user_id === "string" &&
    "email" in value &&
    typeof value.email === "string" &&
    "session_token" in value &&
    typeof value.session_token === "string" &&
    "expires_at" in value &&
    typeof value.expires_at === "string"
  );
}

export async function POST(request: Request) {
  let payload: unknown;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  if (!isVerifyRequest(payload)) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/auth/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: payload.token, purpose: "invite" }),
      cache: "no-store",
      signal: request.signal,
    });

    if (!upstream.ok) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: upstreamResponseHeaders(upstream),
      });
    }

    const verified: unknown = await upstream.json();
    if (!isVerifyResponse(verified)) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const expiresAt = new Date(verified.expires_at);
    if (Number.isNaN(expiresAt.getTime())) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const response = NextResponse.json({ id: verified.user_id, email: verified.email });
    response.cookies.set({
      name: SESSION_COOKIE_NAME,
      value: verified.session_token,
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      expires: expiresAt,
      path: "/",
    });
    return response;
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
