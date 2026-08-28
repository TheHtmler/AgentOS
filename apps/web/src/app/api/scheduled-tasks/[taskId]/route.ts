import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders, proxyUpstreamResponse } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ taskId: string }> };

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

function invalidTaskResponse() {
  return NextResponse.json(
    { error: "invalid_task_id" },
    { status: 400, headers: { "Cache-Control": "no-store" } },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    { status: 503, headers: { "Cache-Control": "no-store" } },
  );
}

async function proxyTaskRequest(
  request: Request,
  taskId: string,
  method: "GET" | "PATCH" | "DELETE",
) {
  try {
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/scheduled-tasks/${taskId}`, {
      method,
      headers:
        method === "PATCH"
          ? { ...(await agentApiSessionHeaders()), "Content-Type": "application/json" }
          : await agentApiSessionHeaders(),
      ...(method === "PATCH" ? { body: await request.text() } : {}),
      cache: "no-store",
      signal: request.signal,
    });
    return proxyUpstreamResponse(upstream);
  } catch {
    return unavailableResponse();
  }
}

export async function GET(request: Request, context: RouteContext) {
  const { taskId } = await context.params;
  return isUuid(taskId) ? proxyTaskRequest(request, taskId, "GET") : invalidTaskResponse();
}

export async function PATCH(request: Request, context: RouteContext) {
  const { taskId } = await context.params;
  return isUuid(taskId) ? proxyTaskRequest(request, taskId, "PATCH") : invalidTaskResponse();
}

export async function DELETE(request: Request, context: RouteContext) {
  const { taskId } = await context.params;
  return isUuid(taskId) ? proxyTaskRequest(request, taskId, "DELETE") : invalidTaskResponse();
}
