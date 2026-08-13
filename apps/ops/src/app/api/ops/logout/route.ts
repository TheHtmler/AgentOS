import { NextResponse } from "next/server";

import {
  OPS_SESSION_COOKIE_NAME,
  agentApiBaseUrl,
  opsSessionHeaders,
  proxyUpstreamResponse,
} from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/logout`, {
      method: "POST",
      headers: await opsSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    const response = proxyUpstreamResponse(upstream);
    const next = new NextResponse(response.body, {
      status: response.status,
      headers: response.headers,
    });
    next.cookies.set({
      name: OPS_SESSION_COOKIE_NAME,
      value: "",
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      expires: new Date(0),
      path: "/",
    });
    return next;
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
