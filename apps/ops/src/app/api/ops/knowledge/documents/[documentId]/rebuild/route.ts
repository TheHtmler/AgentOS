import { NextResponse } from "next/server";
import { agentApiBaseUrl, opsSessionHeaders, proxyUpstreamResponse } from "@/lib/ops-api";

export async function POST(request: Request, context: { params: Promise<{ documentId: string }> }) {
  const { documentId } = await context.params;
  try {
    const payload: unknown = await request.json();
    return proxyUpstreamResponse(
      await fetch(`${agentApiBaseUrl()}/v1/ops/knowledge/documents/${documentId}/rebuild`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await opsSessionHeaders()) },
        body: JSON.stringify(payload),
        cache: "no-store",
        signal: request.signal,
      }),
    );
  } catch {
    return NextResponse.json({ error: "导入服务暂不可用" }, { status: 503 });
  }
}
