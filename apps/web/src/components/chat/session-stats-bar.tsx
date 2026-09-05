"use client";

import { useEffect, useState } from "react";
import { CircleGauge } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

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
  const usagePct = Math.min(100, Math.round((inputTokens / contextWindow) * 100));

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`最近一轮输入 ${inputTokens} / ${contextWindow} tokens`}
          className="relative flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <CircleGauge className={usagePct > 85 ? "size-4 text-destructive" : "size-4"} />
          <span className="sr-only">上下文占用 {usagePct}%</span>
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="end" sideOffset={8} className="z-50 w-60 rounded-xl p-4">
        <div className="flex items-baseline justify-between">
          <p className="text-sm font-medium">上下文</p>
          <p
            className={
              usagePct > 85
                ? "text-sm text-destructive tabular-nums"
                : "text-sm text-muted-foreground tabular-nums"
            }
          >
            {usagePct}%
          </p>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={usagePct > 85 ? "h-full bg-destructive" : "h-full bg-primary"}
            style={{ width: `${usagePct}%` }}
          />
        </div>
        <dl className="mt-3 grid gap-2 text-sm">
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">最近一轮输入</dt>
            <dd className="font-mono text-xs tabular-nums">{(inputTokens / 1_000).toFixed(1)}k</dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">上下文窗口</dt>
            <dd className="font-mono text-xs tabular-nums">
              {(contextWindow / 1_000).toFixed(0)}k
            </dd>
          </div>
        </dl>
      </PopoverContent>
    </Popover>
  );
}
