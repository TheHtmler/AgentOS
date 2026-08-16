import { NextResponse } from "next/server";

type HealthPayload = {
  status: "ok";
};

export const dynamic = "force-dynamic";

function isHealthPayload(value: unknown): value is HealthPayload {
  return typeof value === "object" && value !== null && "status" in value && value.status === "ok";
}

function unavailableResponse(status: 502 | 503) {
  return NextResponse.json(
    { status: "unavailable" },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}

export async function GET() {
  const baseUrl = (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");

  try {
    const response = await fetch(`${baseUrl}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3_000),
    });

    if (!response.ok) {
      return unavailableResponse(502);
    }

    const payload: unknown = await response.json();

    if (!isHealthPayload(payload)) {
      return unavailableResponse(502);
    }

    return NextResponse.json(payload, {
      headers: {
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return unavailableResponse(503);
  }
}
