"use client";

/**
 * AssistantThread — assistant-ui chat surface for the AgentOS chat workspace.
 *
 * Composes the assistant-ui `AssistantRuntimeProvider` + `Thread` over the
 * AG-UI `ExternalStoreRuntime` adapter (`useAguiRuntime`), and mounts the
 * domain components assistant-ui has no equivalent for as slots:
 *   - `ApprovalPanel` — HITL approve/deny (case_slot_collect / tool approval)
 *
 * Exports a props signature compatible with the legacy `ChatPanel` so
 * `ChatWorkspace` can switch mount points with a one-line change.
 *
 * NOTE: `@assistant-ui/react` is not yet installed in this sandbox (no network).
 * The assistant-ui module is loaded lazily via `import()`, so this file
 * type-checks standalone; once the dependency is installed, replace the lazy
 * load with direct imports and drop the `aui` indirection.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { ApprovalPanel, type PendingInterrupt } from "@/components/chat/approval-panel";
import { useAguiRuntime } from "@/lib/agui-runtime";

// ---------------------------------------------------------------------------
// assistant-ui type shims (real types arrive with @assistant-ui/react)
// ---------------------------------------------------------------------------

type AssistantMessageLike = {
  id: string;
  role: "user" | "assistant";
  content: readonly {
    type: string;
    text?: string;
    toolCallId?: string;
    toolName?: string;
    args?: unknown;
  }[];
  createdAt?: Date;
  status?: { type: "running" } | { type: "complete" } | { type: "incomplete" };
};

type AppendMessageLike = {
  content: readonly { type: string; text?: string }[];
  parentId?: string;
  id?: string;
};

// ---------------------------------------------------------------------------
// Approval state (ported from ChatPanel: load / clear / resume)
// ---------------------------------------------------------------------------

type ApprovalState = {
  runId: string | null;
  interrupts: PendingInterrupt[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parsePendingInterrupts(value: unknown): PendingInterrupt[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const items: PendingInterrupt[] = [];

  for (const entry of value) {
    if (
      !isRecord(entry) ||
      typeof entry.id !== "string" ||
      typeof entry.tool_call_id !== "string" ||
      typeof entry.tool_name !== "string" ||
      typeof entry.expires_at !== "string" ||
      !isRecord(entry.tool_args)
    ) {
      continue;
    }
    items.push({
      id: entry.id,
      tool_call_id: entry.tool_call_id,
      tool_name: entry.tool_name,
      tool_args: entry.tool_args,
      expires_at: entry.expires_at,
    });
  }

  return items;
}

async function loadRunApprovalState(runId: string): Promise<ApprovalState | null> {
  try {
    const response = await fetch(`/api/runs/${runId}`, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    if (!isRecord(payload) || typeof payload.status !== "string") {
      return null;
    }
    return {
      runId,
      interrupts: parsePendingInterrupts(payload.pending_interrupts),
    };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Props (mirrors ChatPanel's surface)
// ---------------------------------------------------------------------------

type AssistantThreadProps = {
  selectedThreadId: string | null | undefined;
  agentId: string | null;
  isActive?: boolean;
  isScheduledTaskThread?: boolean;
  onStreamingChanged: (isStreaming: boolean) => void;
  onAwaitingApprovalChanged?: (isAwaiting: boolean) => void;
  onThreadChanged: (threadId: string | null, agentId?: string) => void;
  onRunFinalized: () => void;
  onRunStarted?: (runId: string) => void;
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function AssistantThread(props: AssistantThreadProps) {
  return <AssistantSurface {...props} />;
}

function AssistantSurface({
  isActive = true,
  onStreamingChanged,
  onAwaitingApprovalChanged,
  onThreadChanged,
  onRunFinalized,
  onRunStarted,
  agentId,
}: AssistantThreadProps) {
  const [approval, setApproval] = useState<ApprovalState>({ runId: null, interrupts: [] });
  const lastRunIdRef = useRef<string | null>(null);

  // AG-UI bridge: owns messages + isRunning, drives HttpAgent sends.
  const agui = useAguiRuntime({
    agentId,
    onStreamingChanged,
    onThreadChanged,
    onRunFinalized,
    onRunStarted: (runId) => {
      lastRunIdRef.current = runId;
      onRunStarted?.(runId);
    },
  });

  // Load approval state when a run goes waiting_approval.
  useEffect(() => {
    const runId = lastRunIdRef.current;
    if (runId === null || !isActive) {
      return;
    }

    let cancelled = false;

    void (async () => {
      const state = await loadRunApprovalState(runId);
      if (!cancelled && state !== null && state.interrupts.length > 0) {
        setApproval(state);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [agui.isRunning, isActive]);

  useEffect(() => {
    onAwaitingApprovalChanged?.(approval.interrupts.length > 0);
  }, [approval.interrupts.length, onAwaitingApprovalChanged]);

  const handleApprovalResolved = useMemo(
    () => async () => {
      const runId = approval.runId;
      setApproval({ runId: null, interrupts: [] });
      if (runId === null) {
        return;
      }
      await agui.resumeRun(runId, "");
    },
    [agui, approval.runId],
  );

  // Lazy-load the assistant-ui dependency once; render a fallback until then.
  const [aui, setAui] = useState<Record<string, unknown> | null>(null);
  const [auiError, setAuiError] = useState(false);

  useEffect(() => {
    let disposed = false;

    void (async () => {
      try {
        const mod = await import("@assistant-ui/react");
        if (!disposed) {
          setAui(mod);
        }
      } catch {
        if (!disposed) {
          setAuiError(true);
        }
      }
    })();

    return () => {
      disposed = true;
    };
  }, []);

  // Defer the surface render until assistant-ui resolves.
  if (aui === null) {
    return auiError ? (
      <p className="p-4 text-sm text-muted-foreground">
        聊天组件未就绪：请先安装 @assistant-ui/react。
      </p>
    ) : (
      <p className="p-4 text-sm text-muted-foreground">加载聊天组件…</p>
    );
  }

  return (
    <RenderedThread
      aui={aui}
      approval={approval}
      handleApprovalResolved={handleApprovalResolved}
      agui={agui}
    />
  );
}

type RenderedThreadProps = {
  aui: Record<string, unknown>;
  approval: ApprovalState;
  handleApprovalResolved: () => Promise<void>;
  agui: ReturnType<typeof useAguiRuntime>;
};

function RenderedThread({ aui, approval, handleApprovalResolved, agui }: RenderedThreadProps) {
  const { AssistantRuntimeProvider, Thread, useExternalStoreRuntime } = aui as {
    AssistantRuntimeProvider: (props: {
      runtime: unknown;
      children: React.ReactNode;
    }) => React.ReactNode;
    Thread: (props: Record<string, unknown>) => React.ReactNode;
    useExternalStoreRuntime: (options: Record<string, unknown>) => unknown;
  };

  // ExternalStoreRuntime: bridge the AG-UI store into assistant-ui.
  const runtime = useExternalStoreRuntime({
    messages: agui.messages as AssistantMessageLike[],
    isRunning: agui.isRunning,
    onNew: agui.onNew as (message: AppendMessageLike) => Promise<void>,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex h-full min-h-0 flex-col">
        {approval.interrupts.length > 0 && approval.runId !== null ? (
          <div className="shrink-0 p-3">
            <ApprovalPanel
              runId={approval.runId}
              interrupts={approval.interrupts}
              onResolved={() => void handleApprovalResolved()}
              onError={(message) => console.error(message)}
            />
          </div>
        ) : null}
        <div className="min-h-0 flex-1">
          <Thread />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

export type { AssistantThreadProps };
