"use client";

import { useCallback, useEffect, useState } from "react";

type HealthState = "checking" | "ready" | "unavailable";

type HealthPayload = {
  status: "ok";
};

const POLL_INTERVAL_MS = 30_000;

const stateLabel: Record<HealthState, string> = {
  checking: "检查中",
  ready: "运行正常",
  unavailable: "不可用",
};

const stateColor: Record<HealthState, string> = {
  checking: "bg-amber-500",
  ready: "bg-emerald-500",
  unavailable: "bg-rose-500",
};

function isHealthPayload(value: unknown): value is HealthPayload {
  return typeof value === "object" && value !== null && "status" in value && value.status === "ok";
}

export function HealthStatus() {
  const [state, setState] = useState<HealthState>("checking");
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    try {
      // Browser traffic stays on the Next.js origin; the Route Handler owns upstream access.
      const response = await fetch("/api/health", {
        cache: "no-store",
      });
      const payload: unknown = await response.json();

      setState(response.ok && isHealthPayload(payload) ? "ready" : "unavailable");
    } catch {
      setState("unavailable");
    } finally {
      setLastCheckedAt(new Date());
    }
  }, []);

  useEffect(() => {
    // Schedule the first request after the effect flushes to avoid a synchronous state chain.
    const initialCheckId = window.setTimeout(() => {
      void refresh();
    }, 0);

    // Recheck periodically without keeping an SSE or WebSocket connection open.
    const intervalId = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);

    return () => {
      window.clearTimeout(initialCheckId);
      window.clearInterval(intervalId);
    };
  }, [refresh]);

  return (
    <section
      aria-live="polite"
      className="min-w-0 border border-zinc-200 bg-white p-4 shadow-sm sm:p-5"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-zinc-500">Agent API</p>
          <div className="mt-2 flex items-center gap-2">
            <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${stateColor[state]}`} />
            <p className="text-xl font-semibold text-zinc-950">{stateLabel[state]}</p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => {
            setState("checking");
            void refresh();
          }}
          disabled={state === "checking"}
          className="shrink-0 border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition hover:border-zinc-500 hover:text-zinc-950 disabled:cursor-not-allowed disabled:opacity-50"
        >
          重新检查
        </button>
      </div>

      <p className="mt-5 text-sm text-zinc-500">
        {lastCheckedAt
          ? `上次检查：${lastCheckedAt.toLocaleTimeString("zh-CN")}`
          : "正在连接 Agent API"}
      </p>
    </section>
  );
}
