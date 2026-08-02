import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ runId: string }>;
};

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_run_id" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

export async function GET(request: Request, context: RouteContext) {
  const { runId } = await context.params;

  if (!isUuid(runId)) {
    return invalidRequestResponse();
  }

  const baseUrl = (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

  try {
    const upstream = await fetch(`${baseUrl}/v1/runs/${runId}`, {
      cache: "no-store",
      signal: request.signal,
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store" });
    const contentType = upstream.headers.get("content-type");

    if (contentType !== null) {
      responseHeaders.set("Content-Type", contentType);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return unavailableResponse();
  }
}
