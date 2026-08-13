import { NextResponse } from "next/server";

import { OPS_SESSION_COOKIE_NAME, agentApiBaseUrl, upstreamResponseHeaders } from "@/lib/ops-api";

type LoginRequest = {
  username: string;
  password: string;
};

type OpsLoginResponse = {
  subject: string;
  session_token: string;
  expires_at: string;
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function isLoginRequest(value: unknown): value is LoginRequest {
  return (
    typeof value === "object" &&
    value !== null &&
    "username" in value &&
    typeof value.username === "string" &&
    "password" in value &&
    typeof value.password === "string"
  );
}

function isOpsLoginResponse(value: unknown): value is OpsLoginResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "subject" in value &&
    typeof value.subject === "string" &&
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

  if (!isLoginRequest(payload)) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: payload.username,
        password: payload.password,
      }),
      cache: "no-store",
      signal: request.signal,
    });

    if (!upstream.ok) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: upstreamResponseHeaders(upstream),
      });
    }

    const authSession: unknown = await upstream.json();
    if (!isOpsLoginResponse(authSession)) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const expiresAt = new Date(authSession.expires_at);
    if (Number.isNaN(expiresAt.getTime())) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const response = NextResponse.json({ subject: authSession.subject });
    response.cookies.set({
      name: OPS_SESSION_COOKIE_NAME,
      value: authSession.session_token,
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
