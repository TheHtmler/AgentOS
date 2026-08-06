import { NextResponse } from "next/server";

import { agentApiBaseUrl, agentApiSessionHeaders } from "@/lib/agent-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function invalidRequestResponse() {
  return NextResponse.json(
    { error: "invalid_request" },
    {
      status: 400,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

function unavailableResponse() {
  return NextResponse.json(
    { error: "agent_api_unavailable" },
    {
      status: 503,
      headers: { "Cache-Control": "no-store" },
    },
  );
}

export async function POST(request: Request) {
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return invalidRequestResponse();
  }

  let body: string;

  try {
    body = await request.text();
  } catch {
    return invalidRequestResponse();
  }

  if (!body || body.length > 1_000_000) {
    return invalidRequestResponse();
  }

  try {
    const agentId = request.headers.get("x-agentos-agent-id");
    const caseId = request.headers.get("x-agentos-case-id");
    const upstream = await fetch(`${agentApiBaseUrl()}/v1/ag-ui/runs`, {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        ...(await agentApiSessionHeaders()),
        ...(agentId === null ? {} : { "X-AgentOS-Agent-Id": agentId }),
        ...(caseId === null ? {} : { "X-AgentOS-Case-Id": caseId }),
      },
      body,
      cache: "no-store",
      signal: request.signal,
    });

    const contentType = upstream.headers.get("content-type") ?? "application/json";
    const responseHeaders = new Headers({
      "Cache-Control": upstream.ok ? "no-cache, no-transform" : "no-store",
      "Content-Type": contentType,
    });

    if (!upstream.ok || upstream.body === null) {
      return new Response(upstream.body, {
        status: upstream.status,
        headers: responseHeaders,
      });
    }

    responseHeaders.set("X-Accel-Buffering", "no");

    const threadId = upstream.headers.get("x-agentos-thread-id");
    const runId = upstream.headers.get("x-agentos-run-id");

    if (threadId !== null) {
      responseHeaders.set("X-AgentOS-Thread-ID", threadId);
    }

    if (runId !== null) {
      responseHeaders.set("X-AgentOS-Run-ID", runId);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return unavailableResponse();
  }
}
