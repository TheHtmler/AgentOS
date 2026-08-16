import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = {
  params: Promise<{ artifactId: string }>;
};

function isUuid(value: string): boolean {
  const parts = value.split("-");

  return (
    parts.length === 5 &&
    parts[0].length === 8 &&
    parts[1].length === 4 &&
    parts[2].length === 4 &&
    parts[3].length === 4 &&
    parts[4].length === 12 &&
    parts.every((part) => /^[0-9a-f]+$/i.test(part))
  );
}

export async function GET(_request: Request, context: RouteContext) {
  const { artifactId } = await context.params;

  if (!isUuid(artifactId)) {
    return NextResponse.json(
      { error: "invalid_artifact_id" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/uploads/${artifactId}/content`, {
      method: "GET",
      headers: {
        ...(await agentApiSessionHeaders()),
      },
      cache: "no-store",
    });

    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json(
      { error: "agent_api_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
