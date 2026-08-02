import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function invalidLimitResponse() {
  return NextResponse.json(
    { error: "invalid_limit" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

function requestedLimit(request: Request): number | null {
  const value = new URL(request.url).searchParams.get("limit");

  if (value === null) {
    return 20;
  }

  if (!/^(?:[1-9]|[1-4][0-9]|50)$/.test(value)) {
    return null;
  }

  return Number(value);
}

export async function GET(request: Request) {
  const limit = requestedLimit(request);

  if (limit === null) {
    return invalidLimitResponse();
  }

  const baseUrl = (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

  try {
    const upstream = await fetch(`${baseUrl}/v1/threads?limit=${limit}`, {
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
