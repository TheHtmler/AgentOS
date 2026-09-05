"use client";

import { useEffect, useState } from "react";

import { ComposerContext } from "@/components/assistant-ui/elements/composer";

type ThreadStats = {
  last_run: {
    input_tokens: number | null;
    context_window: number | null;
  } | null;
};

type ComposerContextUsageProps = {
  threadId: string | null;
  isStreaming: boolean;
  refreshKey: number;
};

/**
 * The API currently exposes an exact aggregate input count, not a reliable
 * system/tool/history split. Feed it as one message segment so the context
 * ring remains truthful rather than presenting made-up category totals.
 */
export function ComposerContextUsage({
  threadId,
  isStreaming,
  refreshKey,
}: ComposerContextUsageProps) {
  const [stats, setStats] = useState<ThreadStats | null>(null);

  useEffect(() => {
    if (threadId === null || isStreaming) return;

    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/threads/${threadId}/stats`, { cache: "no-store" });
        if (!response.ok) return;
        if (!cancelled) setStats((await response.json()) as ThreadStats);
      } catch {
        // Context usage is informational and should never block the composer.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId, isStreaming, refreshKey]);

  const inputTokens = stats?.last_run?.input_tokens;
  const contextWindow = stats?.last_run?.context_window;
  if (inputTokens === null || inputTokens === undefined || !contextWindow) return null;

  return (
    <ComposerContext
      usage={{
        system: 0,
        tools: 0,
        messages: inputTokens / 1_000,
        total: contextWindow / 1_000,
      }}
      className="z-30 shrink-0"
      aria-label={`最近一轮输入 ${inputTokens} / ${contextWindow} tokens`}
    />
  );
}
