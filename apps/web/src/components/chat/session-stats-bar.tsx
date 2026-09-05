"use client";

import { useEffect, useState } from "react";

import { ComposerContext } from "@/components/assistant-ui/elements/composer";

export type ThreadStats = {
  runs_total: number;
  tool_calls_total: number;
  input_tokens_total: number;
  output_tokens_total: number;
  model_time_ms_total: number;
  tool_time_ms_total: number;
  ttft_ms_avg: number | null;
  last_run: {
    input_tokens: number | null;
    cached_input_tokens: number | null;
    context_window: number | null;
  } | null;
};

type ComposerContextUsageProps = {
  threadId: string | null;
  isStreaming: boolean;
  refreshKey: number;
};

function formatCompact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function formatDurationMs(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1_000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}m` : `${minutes}m${seconds}s`;
}

/**
 * Uses the assistant-ui Context element for the token ring and standard
 * breakdown. AgentOS session metrics are supplementary domain observability,
 * mounted through its details slot rather than replacing the element.
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
        // Observability is informational and must never block the composer.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId, isStreaming, refreshKey]);

  if (stats === null || stats.runs_total === 0) return null;
  const lastRun = stats.last_run;
  if (lastRun === null || lastRun.input_tokens === null || !lastRun.context_window) return null;

  const cachePct =
    lastRun.cached_input_tokens !== null && lastRun.input_tokens > 0
      ? Math.round((lastRun.cached_input_tokens / lastRun.input_tokens) * 100)
      : null;

  return (
    <ComposerContext
      usage={{
        system: 0,
        tools: 0,
        messages: lastRun.input_tokens / 1_000,
        total: lastRun.context_window / 1_000,
      }}
      className="shrink-0"
      aria-label={`最近一轮输入 ${lastRun.input_tokens} / ${lastRun.context_window} tokens`}
      details={
        <div className="grid gap-2 text-[13px] text-foreground/55">
          <p className="font-medium text-foreground">会话观测</p>
          <p>{`${stats.runs_total} 轮 · ${stats.tool_calls_total} 步`}</p>
          <p>{`LLM ${formatDurationMs(stats.model_time_ms_total)} · 工具 ${formatDurationMs(stats.tool_time_ms_total)}`}</p>
          {stats.ttft_ms_avg !== null ? (
            <p>{`首 token 平均 ${formatDurationMs(stats.ttft_ms_avg)}`}</p>
          ) : null}
          <p>{`累计输入 ${formatCompact(stats.input_tokens_total)} · 输出 ${formatCompact(stats.output_tokens_total)} tok`}</p>
          {cachePct !== null ? <p>{`缓存命中 ${cachePct}%`}</p> : null}
        </div>
      }
    />
  );
}
