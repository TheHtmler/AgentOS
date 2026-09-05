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
 */

import {
  AssistantRuntimeProvider,
  type DictationAdapter,
  useExternalStoreRuntime,
  WebSpeechDictationAdapter,
} from "@assistant-ui/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ApprovalPanel, type PendingInterrupt } from "@/components/chat/approval-panel";
import { useAguiRuntime } from "@/lib/agui-runtime";

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
  composerFooter?: ReactNode;
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
  composerFooter,
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

  const dictationAdapter = useMemo<DictationAdapter>(() => new WebSpeechDictationAdapter(), []);
  const runtime = useExternalStoreRuntime({
    messages: agui.messages,
    isRunning: agui.isRunning,
    onNew: agui.onNew,
    // Messages are already in ThreadMessageLike shape; no conversion needed.
    convertMessage: (message) => message,
    adapters: { dictation: dictationAdapter },
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
          <Thread composerFooter={composerFooter} />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

export type { AssistantThreadProps };
