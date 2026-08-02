import { NextResponse } from "next/server";

type ChatRequest = {
  message: string;
  threadId?: string;
};

type AgentApiChatRequest = {
  message: string;
  thread_id?: string;
};

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

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function parseChatRequest(value: unknown): ChatRequest | null {
  if (
    typeof value !== "object" ||
    value === null ||
    !("message" in value) ||
    typeof value.message !== "string"
  ) {
    return null;
  }

  // Keep proxy-side limits aligned with FastAPI before opening an upstream stream.
  if (!value.message.trim() || value.message.length > 4_000) {
    return null;
  }

  const threadId = "threadId" in value ? value.threadId : undefined;
  if (threadId !== undefined && (typeof threadId !== "string" || !isUuid(threadId))) {
    return null;
  }

  return threadId === undefined ? { message: value.message } : { message: value.message, threadId };
}

export async function POST(request: Request) {
  let payload: unknown;

  try {
    payload = await request.json();
  } catch {
    return invalidRequestResponse();
  }

  const chatRequest = parseChatRequest(payload);
  if (chatRequest === null) {
    return invalidRequestResponse();
  }

  const agentApiRequest: AgentApiChatRequest = {
    message: chatRequest.message,
    ...(chatRequest.threadId === undefined ? {} : { thread_id: chatRequest.threadId }),
  };

  const baseUrl = (process.env.AGENT_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

  try {
    const upstream = await fetch(`${baseUrl}/v1/chat/stream`, {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(agentApiRequest),
      cache: "no-store",
      // Propagate browser cancellation to the Agent API and model stream.
      signal: request.signal,
    });

    if (!upstream.ok || upstream.body === null) {
      return unavailableResponse();
    }

    const responseHeaders = new Headers({
      "Cache-Control": "no-cache, no-transform",
      "Content-Type": "text/event-stream; charset=utf-8",
      "X-Accel-Buffering": "no",
    });
    const threadId = upstream.headers.get("x-agentos-thread-id");

    // The browser needs this durable identity before it can continue the same Thread.
    if (threadId !== null) {
      responseHeaders.set("X-AgentOS-Thread-ID", threadId);
    }

    return new Response(upstream.body, { headers: responseHeaders });
  } catch {
    return unavailableResponse();
  }
}
