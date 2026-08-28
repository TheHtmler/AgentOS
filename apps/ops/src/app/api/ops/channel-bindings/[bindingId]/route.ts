import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ bindingId: string }>;
};

async function readBindingId(context: RouteContext): Promise<string> {
  const { bindingId } = await context.params;
  return encodeURIComponent(bindingId);
}

export async function PATCH(request: Request, context: RouteContext) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid_request" }, { status: 400 });
  }

  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/channel-bindings/${await readBindingId(context)}`,
      {
        method: "PATCH",
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
  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/channel-bindings/${await readBindingId(context)}`,
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
