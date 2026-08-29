import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ userId: string }>;
};

async function readUserId(context: RouteContext): Promise<string> {
  const { userId } = await context.params;
  return encodeURIComponent(userId);
}

export async function PATCH(request: Request, context: RouteContext) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/users/${await readUserId(context)}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...(await opsSessionHeaders()),
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
