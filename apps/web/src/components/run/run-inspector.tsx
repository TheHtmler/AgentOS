"use client";

import { useEffect, useState } from "react";

type RunDetail = {
  id: string;
  thread_id: string;
  status: "queued" | "running" | "waiting_approval" | "completed" | "failed" | "cancelled";
  model_name: string;
  input_tokens: number | null;
  output_tokens: number | null;
  model_request_count: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

type RunInspectorProps = {
  runId: string | null;
};

const statusLabel: Record<RunDetail["status"], string> = {
  queued: "等待中",
  running: "运行中",
  waiting_approval: "待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已停止",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRunDetail(value: unknown): value is RunDetail {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.thread_id === "string" &&
    ["queued", "running", "waiting_approval", "completed", "failed", "cancelled"].includes(
      typeof value.status === "string" ? value.status : "",
    ) &&
    typeof value.model_name === "string" &&
    (typeof value.input_tokens === "number" || value.input_tokens === null) &&
    (typeof value.output_tokens === "number" || value.output_tokens === null) &&
    (typeof value.model_request_count === "number" || value.model_request_count === null) &&
    typeof value.created_at === "string" &&
    (typeof value.started_at === "string" || value.started_at === null) &&
    (typeof value.completed_at === "string" || value.completed_at === null)
  );
}

function isTerminalStatus(status: RunDetail["status"]): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "cancelled" ||
    status === "waiting_approval"
  );
}

function formatDuration(run: RunDetail): string {
  const startAt = run.started_at ?? run.created_at;
  const endAt = run.completed_at;

  if (endAt === null) {
    return "进行中";
  }

  const milliseconds = new Date(endAt).getTime() - new Date(startAt).getTime();

  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return "-";
  }

  const seconds = milliseconds / 1_000;

  if (seconds < 60) {
    return `${seconds.toFixed(1)} 秒`;
  }

  return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
}

function RunDetailPanel({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isCurrent = true;
    let pollTimeoutId: number | null = null;
    const controller = new AbortController();

    const readRun = async () => {
      try {
        const response = await fetch(`/api/runs/${runId}`, {
          cache: "no-store",
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error("无法读取 Run 状态。");
        }

        const payload: unknown = await response.json();

        if (!isRunDetail(payload)) {
          throw new Error("Run 状态格式无效。");
        }

        if (!isCurrent) {
          return;
        }

        setRun(payload);
        setError(null);

        // Only poll while the backend can still change this Run's final metrics.
        if (!isTerminalStatus(payload.status)) {
          pollTimeoutId = window.setTimeout(() => {
            void readRun();
          }, 750);
        }
      } catch (caughtError: unknown) {
        if (!isCurrent || controller.signal.aborted) {
          return;
        }

        setError(caughtError instanceof Error ? caughtError.message : "无法读取 Run 状态。");
      }
    };

    void readRun();

    return () => {
      isCurrent = false;
      controller.abort();

      if (pollTimeoutId !== null) {
        window.clearTimeout(pollTimeoutId);
      }
    };
  }, [runId]);

  return (
    <section className="min-w-0 overflow-hidden border border-zinc-200 bg-white p-4 sm:p-5">
      <p className="text-sm font-medium text-zinc-500">当前 Run</p>

      {error ? (
        <p role="alert" className="mt-4 text-sm text-rose-700">
          {error}
        </p>
      ) : run === null ? (
        <p className="mt-4 text-sm text-zinc-500">正在读取执行状态。</p>
      ) : (
        <>
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-lg font-semibold text-zinc-950">{statusLabel[run.status]}</p>
            <span className="border border-zinc-300 px-2 py-1 text-xs text-zinc-600">
              {formatDuration(run)}
            </span>
          </div>

          <dl className="mt-4 divide-y divide-zinc-100">
            <div className="py-3 first:pt-0">
              <dt className="text-xs text-zinc-500">模型</dt>
              <dd className="mt-1 text-sm font-medium break-all text-zinc-800">{run.model_name}</dd>
            </div>
            <div className="py-3">
              <dt className="text-xs text-zinc-500">输入 token</dt>
              <dd className="mt-1 text-sm font-medium text-zinc-800">{run.input_tokens ?? "-"}</dd>
            </div>
            <div className="py-3">
              <dt className="text-xs text-zinc-500">输出 token</dt>
              <dd className="mt-1 text-sm font-medium text-zinc-800">{run.output_tokens ?? "-"}</dd>
            </div>
            <div className="py-3">
              <dt className="text-xs text-zinc-500">模型请求次数</dt>
              <dd className="mt-1 text-sm font-medium text-zinc-800">
                {run.model_request_count ?? "-"}
              </dd>
            </div>
            <div className="py-3 last:pb-0">
              <dt className="text-xs text-zinc-500">Run ID</dt>
              <dd className="mt-1 truncate font-mono text-xs text-zinc-600" title={run.id}>
                {run.id}
              </dd>
            </div>
          </dl>
        </>
      )}
    </section>
  );
}

export function RunInspector({ runId }: RunInspectorProps) {
  if (runId === null) {
    return (
      <section className="min-w-0 overflow-hidden border border-zinc-200 bg-white p-4 sm:p-5">
        <p className="text-sm font-medium text-zinc-500">当前 Run</p>
        <p className="mt-4 text-sm text-zinc-500">发送消息后显示本次执行信息。</p>
      </section>
    );
  }

  return <RunDetailPanel key={runId} runId={runId} />;
}
