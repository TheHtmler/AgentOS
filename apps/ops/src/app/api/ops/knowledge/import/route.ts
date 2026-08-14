import { NextResponse } from "next/server";

import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type") ?? "application/json";
  const headers = new Headers(await opsSessionHeaders());
  headers.set("Content-Type", contentType);

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ops/knowledge/import`, {
      method: "POST",
      headers,
      body: await request.arrayBuffer(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json({ error: "agent_api_unavailable" }, { status: 503 });
  }
}
