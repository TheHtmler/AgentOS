import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, upstreamResponseHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 120;

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_request" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: Request) {
  if (!request.headers.get("content-type")?.includes("multipart/form-data")) {
    return invalidRequestResponse();
  }

  let formData: FormData;

  try {
    formData = await request.formData();
  } catch {
    return invalidRequestResponse();
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/uploads`, {
      method: "POST",
      headers: {
        ...(await agentApiSessionHeaders()),
      },
      body: formData,
      cache: "no-store",
      signal: request.signal,
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: upstreamResponseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}
