import { NextResponse } from "next/server";

import { agentApiBaseUrl, SESSION_COOKIE_NAME, upstreamResponseHeaders } from "@/lib/agent-api";

type RegisterRequest = {
  token: string;
  password: string;
};

type AuthSessionResponse = {
  user_id: string;
  email: string;
  session_token: string;
  expires_at: string;
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function isRegisterRequest(value: unknown): value is RegisterRequest {
  return (
    typeof value === "object" &&
    value !== null &&
    "token" in value &&
    typeof value.token === "string" &&
    value.token.length > 0 &&
    "password" in value &&
    typeof value.password === "string"
  );
}

function isAuthSessionResponse(value: unknown): value is AuthSessionResponse {
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

  if (!isRegisterRequest(payload)) {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: payload.token,
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
    if (!isAuthSessionResponse(authSession)) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const expiresAt = new Date(authSession.expires_at);
    if (Number.isNaN(expiresAt.getTime())) {
      return NextResponse.json({ error: "invalid_upstream_response" }, { status: 502 });
    }

    const response = NextResponse.json({
      id: authSession.user_id,
      email: authSession.email,
    });

    // Registration succeeds only after the backend consumes the invite token.
    response.cookies.set({
      name: SESSION_COOKIE_NAME,
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