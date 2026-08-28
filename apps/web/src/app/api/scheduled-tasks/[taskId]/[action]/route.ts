import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ taskId: string; action: string }> };

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

const actions = new Set(["pause", "resume", "read", "run"]);

export async function POST(request: Request, context: RouteContext) {
  const { taskId, action } = await context.params;
  if (!isUuid(taskId) || !actions.has(action)) {
    return NextResponse.json(
      { error: "invalid_scheduled_task_request" },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/scheduled-tasks/${taskId}/${action}`, {
      method: "POST",
      headers: await agentApiSessionHeaders(),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return NextResponse.json(
      { error: "agent_api_unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
