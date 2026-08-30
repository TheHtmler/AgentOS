import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 120;

export async function POST(request: Request) {
  if (!request.headers.get("content-type")?.includes("multipart/form-data")) {
    return NextResponse.json(
      { error: "invalid_request" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const formData = await request.formData();
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/audio/transcriptions`, {
      method: "POST",
      headers: { ...(await agentApiSessionHeaders()) },
      body: formData,
      cache: "no-store",
      signal: request.signal,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return NextResponse.json(
      { error: "agent_api_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
