import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request, { params }: { params: Promise<{ loginId: string }> }) {
  try {
    const { loginId } = await params;
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/channel-bindings/weixin-login/${loginId}`,
      {
        headers: await agentApiSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
