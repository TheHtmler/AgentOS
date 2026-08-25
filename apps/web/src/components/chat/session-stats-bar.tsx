"use client";

import { useEffect, useState } from "react";

export type ThreadStats = {
  runs_total: number;
  tool_calls_total: number;
  input_tokens_total: number;
  output_tokens_total: number;
  model_time_ms_total: number;
  tool_time_ms_total: number;
  ttft_ms_avg: number | null;
  last_run: {
    id: string;
    status: string;
    input_tokens: number | null;
    output_tokens: number | null;
    ttft_ms: number | null;
    cached_input_tokens: number | null;
    context_window: number | null;
  } | null;
};

export type LiveRunStats = {
  startedAt: number;
  firstTokenMs: number | null;
};

type SessionStatsBarProps = {
  threadId: string | null;
  isStreaming: boolean;
  // Bumped whenever durable history is reloaded; doubles as the stats refetch signal.
  refreshKey: number;
  liveStats: LiveRunStats | null;
  liveToolCalls: number;
  liveAssistantChars: number;
};

function formatCompact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

function formatDurationMs(ms: number): string {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return seconds === 0 ? `${minutes}m` : `${minutes}m${seconds}s`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes === 0 ? `${hours}h` : `${hours}h${restMinutes}m`;
}

export function SessionStatsBar({
  threadId,
  isStreaming,
  refreshKey,
  liveStats,
  liveToolCalls,
  liveAssistantChars,
}: SessionStatsBarProps) {
  const [stats, setStats] = useState<ThreadStats | null>(null);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    if (threadId === null || isStreaming) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(`/api/threads/${threadId}/stats`, { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        if (!cancelled) {
          setStats((await response.json()) as ThreadStats);
        }
      } catch {
        // Informational only: keep the previous figures on failure.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId, isStreaming, refreshKey]);

  useEffect(() => {
    if (!isStreaming) {
      return;
    }
    const update = () => setNow(Date.now());
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [isStreaming]);

  if (isStreaming) {
    if (liveStats === null) {
      return null;
    }
    const elapsedMs = Math.max(0, (now ?? liveStats.startedAt) - liveStats.startedAt);
    const parts = [`本轮 ${formatDurationMs(elapsedMs)}`, `工具 ${liveToolCalls} 次`];
    if (liveStats.firstTokenMs !== null) {
      parts.push(`首 token ${formatDurationMs(liveStats.firstTokenMs)}`);
      const generatingMs = elapsedMs - liveStats.firstTokenMs;
      if (generatingMs > 0 && liveAssistantChars > 0) {
        const approxTokensPerSecond = liveAssistantChars / 4 / (generatingMs / 1000);
        parts.push(`约 ${formatCompact(Math.max(1, Math.round(approxTokensPerSecond)))} tok/s`);
      }
    }
    return (
      <div className="agentos-stats" aria-live="off">
        <span className="agentos-stats-line">{parts.join(" · ")}</span>
      </div>
    );
  }

  if (stats === null || stats.runs_total === 0) {
    return null;
  }

  const lastRun = stats.last_run;
  const contextPct =
    lastRun !== null && lastRun.input_tokens !== null && lastRun.context_window
      ? Math.min(100, Math.round((lastRun.input_tokens / lastRun.context_window) * 100))
      : null;
  const cachePct =
    lastRun !== null && lastRun.cached_input_tokens !== null && lastRun.input_tokens
      ? Math.round((lastRun.cached_input_tokens / lastRun.input_tokens) * 100)
      : null;

  const parts = [
    `${stats.runs_total} 轮 · ${stats.tool_calls_total} 步`,
    `LLM ${formatDurationMs(stats.model_time_ms_total)} · 工具 ${formatDurationMs(stats.tool_time_ms_total)}`,
  ];
  if (stats.ttft_ms_avg !== null) {
    parts.push(`首 token 平均 ${formatDurationMs(stats.ttft_ms_avg)}`);
  }
  parts.push(
    `输入 ${formatCompact(stats.input_tokens_total)} · 输出 ${formatCompact(stats.output_tokens_total)} tok`,
  );
  if (cachePct !== null) {
    parts.push(`缓存命中 ${cachePct}%`);
  }

  return (
    <div className="agentos-stats" aria-live="off">
      <span className="agentos-stats-line">{parts.join(" | ")}</span>
      {contextPct !== null && lastRun !== null ? (
        <span
          className="agentos-stats-context"
          title={`最近一轮输入 ${lastRun.input_tokens ?? 0} / ${lastRun.context_window ?? 0} tokens`}
        >
          <span>上下文</span>
          <span className="agentos-stats-track">
            <span
              className={`agentos-stats-fill${
                contextPct > 95
                  ? "agentos-stats-fill-danger"
                  : contextPct > 80
                    ? "agentos-stats-fill-warn"
                    : ""
              }`}
              style={{ width: `${contextPct}%` }}
            />
          </span>
          <span className="agentos-stats-context-value">{contextPct}%</span>
        </span>
      ) : null}
    </div>
  );
}
