import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ bindingId: string }>;
};

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function DELETE(request: Request, context: RouteContext) {
  try {
    const { bindingId } = await context.params;
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/channel-bindings/${bindingId}`, {
      method: "DELETE",
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return unavailableResponse();
  }
}
