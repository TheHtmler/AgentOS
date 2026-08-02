import { NextResponse } from "next/server";

import {
  agentApiBaseUrl,
  agentApiSessionHeaders,
  SESSION_COOKIE_NAME,
  upstreamResponseHeaders,
} from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/auth/logout`, {
      method: "POST",
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    const response = new NextResponse(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
    response.cookies.set({ name: SESSION_COOKIE_NAME, value: "", maxAge: 0, path: "/" });
    return response;
  } catch {
    const response = NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
    response.cookies.set({ name: SESSION_COOKIE_NAME, value: "", maxAge: 0, path: "/" });
    return response;
  }
}
