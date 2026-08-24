import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_sandbox_file_path" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const path = url.searchParams.get("path");
  if (path === null || path.trim() === "" || path.length > 512) {
    return invalidRequestResponse();
  }

  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/sandboxes/files?${new URLSearchParams({
        path,
        download: url.searchParams.get("download") === "1" ? "true" : "false",
      })}`,
      {
        headers: await agentApiSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}
