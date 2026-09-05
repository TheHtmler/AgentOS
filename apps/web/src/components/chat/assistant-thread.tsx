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

import { AssistantRuntimeProvider, useExternalStoreRuntime } from "@assistant-ui/react";
import { Mic, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ApprovalPanel, type PendingInterrupt } from "@/components/chat/approval-panel";
import { AgentOsToolFallback } from "@/components/chat/agentos-tool-fallback";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { SessionStatsBar } from "@/components/chat/session-stats-bar";
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

function VoiceTranscriptionButton({
  onTranscript,
}: {
  onTranscript: (text: string) => Promise<void>;
}) {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);

  useEffect(
    () => () => {
      const recorder = recorderRef.current;
      recorder?.stream.getTracks().forEach((track) => track.stop());
      if (recorder?.state === "recording") recorder.stop();
    },
    [],
  );

  async function toggleRecording() {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      recorder.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const nextRecorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      nextRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      nextRecorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        recorderRef.current = null;
        setRecording(false);
        if (chunks.length === 0) return;
        void (async () => {
          setTranscribing(true);
          try {
            const mimeType = nextRecorder.mimeType || "audio/webm";
            const formData = new FormData();
            formData.append(
              "file",
              new File([new Blob(chunks, { type: mimeType })], "recording.webm", {
                type: mimeType,
              }),
            );
            const response = await fetch("/api/audio/transcriptions", {
              method: "POST",
              body: formData,
            });
            const payload: unknown = await response.json().catch(() => null);
            const text =
              typeof payload === "object" &&
              payload !== null &&
              "text" in payload &&
              typeof payload.text === "string"
                ? payload.text.trim()
                : "";
            if (response.ok && text) await onTranscript(text);
          } finally {
            setTranscribing(false);
          }
        })();
      };
      nextRecorder.start();
      recorderRef.current = nextRecorder;
      setRecording(true);
    } catch {
      // Browser permission UI is the actionable error surface for microphone access.
    }
  }

  return (
    <TooltipIconButton
      type="button"
      tooltip={transcribing ? "正在转写" : recording ? "停止录音并发送" : "按一下开始语音输入"}
      onClick={() => void toggleRecording()}
      disabled={transcribing}
      className={recording ? "size-7 rounded-full text-destructive" : "size-7 rounded-full"}
      aria-label={recording ? "停止录音并发送" : "语音输入"}
    >
      {recording ? <Square className="size-3 fill-current" /> : <Mic className="size-4" />}
    </TooltipIconButton>
  );
}

function AssistantSurface({
  selectedThreadId,
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
    selectedThreadId,
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

  const runtime = useExternalStoreRuntime({
    messages: agui.messages,
    isRunning: agui.isRunning,
    isLoading: agui.isLoading,
    onNew: agui.onNew,
    // Messages are already in ThreadMessageLike shape; no conversion needed.
    convertMessage: (message) => message,
    onCancel: agui.cancelRun,
    onRefetchThread: async () => agui.refreshHistory(),
    adapters: { attachments: agui.attachmentAdapter },
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
          <Thread
            components={{ ToolFallback: AgentOsToolFallback }}
            composerFooter={
              <div className="flex min-w-0 items-center gap-1.5">
                {composerFooter}
                <VoiceTranscriptionButton onTranscript={agui.sendText} />
              </div>
            }
          />
        </div>
        <SessionStatsBar
          threadId={selectedThreadId ?? null}
          isStreaming={agui.isRunning}
          refreshKey={agui.historyVersion}
          liveStats={null}
          liveToolCalls={0}
          liveAssistantChars={0}
        />
      </div>
    </AssistantRuntimeProvider>
  );
}

export type { AssistantThreadProps };
