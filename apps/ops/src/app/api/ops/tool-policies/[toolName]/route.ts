import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ toolName: string }>;
};

export async function PUT(request: Request, context: RouteContext) {
  const { toolName } = await context.params;
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/tool-policies/${encodeURIComponent(toolName)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          ...(await opsSessionHeaders()),
        },
        body: JSON.stringify(payload),
        cache: "no-store",
        signal: request.signal,
      },
    );
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}

export async function DELETE(request: Request, context: RouteContext) {
  const { toolName } = await context.params;
  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/tool-policies/${encodeURIComponent(toolName)}`,
      {
        method: "DELETE",
        headers: await opsSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
