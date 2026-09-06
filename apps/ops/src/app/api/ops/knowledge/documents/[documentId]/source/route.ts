import { NextResponse } from "next/server";
import { agentApiBaseUrl, opsSessionHeaders, upstreamResponseHeaders } from "@/lib/ops-api";

export async function GET(request: Request, context: { params: Promise<{ documentId: string }> }) {
  const { documentId } = await context.params;
  try {
    const upstream = await fetch(
      `${agentApiBaseUrl()}/v1/ops/knowledge/documents/${documentId}/source`,
      {
        headers: await opsSessionHeaders(),
        cache: "no-store",
        signal: request.signal,
      },
    );
    const headers = upstreamResponseHeaders(upstream);
    headers.set("Content-Disposition", upstream.headers.get("Content-Disposition") ?? "attachment");
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return NextResponse.json({ error: "原件暂不可用" }, { status: 503 });
  }
}
