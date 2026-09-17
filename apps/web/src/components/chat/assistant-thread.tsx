"use client";

import { AssistantRuntimeProvider, AuiConfig, Tools, useAuiState } from "@assistant-ui/react";
import { useAgUiInterrupts } from "@assistant-ui/react-ag-ui";
import { useEffect, useMemo, useRef, type ReactNode } from "react";

import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { AgentOsAssistantMessage } from "@/components/chat/agentos-assistant-message";
import { AgentOsToolFallback } from "@/components/chat/agentos-tool-fallback";
import { agentOsToolkit } from "@/components/chat/agentos-toolkit";
import { AgentOsUserMessage } from "@/components/chat/agentos-user-message";
import { AudioTranscriptionDictationAdapter } from "@/components/chat/audio-dictation-adapter";
import { ComposerDictationVoice } from "@/components/chat/composer-dictation-voice";
import { ComposerContextUsage } from "@/components/chat/session-stats-bar";
import { useAgentOsAssistantRuntime } from "@/lib/agentos-assistant-runtime";
import { AgentOsInterruptPayloadProvider } from "@/lib/agentos-interrupt-payloads";

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

function RuntimeEffects({
  isActive,
  onStreamingChanged,
  onAwaitingApprovalChanged,
  onRunFinalized,
  onSettled,
}: {
  isActive: boolean;
  onStreamingChanged: (isStreaming: boolean) => void;
  onAwaitingApprovalChanged?: (isAwaiting: boolean) => void;
  onRunFinalized: () => void;
  onSettled: () => void;
}) {
  const isRunning = useAuiState((state) => state.thread.isRunning);
  const interrupts = useAgUiInterrupts();
  const wasRunning = useRef(false);

  useEffect(() => {
    onStreamingChanged(isRunning);
    if (wasRunning.current && !isRunning) {
      onSettled();
      onRunFinalized();
    }
    wasRunning.current = isRunning;
  }, [isRunning, onRunFinalized, onSettled, onStreamingChanged]);

  useEffect(() => {
    onAwaitingApprovalChanged?.(isActive && interrupts.length > 0);
  }, [interrupts.length, isActive, onAwaitingApprovalChanged]);

  return null;
}

function RuntimeComposerFooter({
  selectedThreadId,
  refreshKey,
  children,
}: {
  selectedThreadId: string | null | undefined;
  refreshKey: number;
  children?: ReactNode;
}) {
  const isRunning = useAuiState((state) => state.thread.isRunning);
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <ComposerDictationVoice />
      {children}
      <ComposerContextUsage
        threadId={selectedThreadId ?? null}
        isStreaming={isRunning}
        refreshKey={refreshKey}
      />
    </div>
  );
}

export function AssistantThread({
  selectedThreadId,
  agentId,
  isActive = true,
  onStreamingChanged,
  onAwaitingApprovalChanged,
  onThreadChanged,
  onRunFinalized,
  onRunStarted,
  composerFooter,
}: AssistantThreadProps) {
  const dictationAdapter = useMemo(() => new AudioTranscriptionDictationAdapter(), []);
  const { runtime, historyVersion, refreshHistory, interruptPayloads } = useAgentOsAssistantRuntime(
    {
      selectedThreadId,
      agentId,
      onThreadChanged,
      onRunStarted,
      dictationAdapter,
      onError: (message) => console.error(message),
    },
  );
  const config = useMemo(() => AuiConfig({ tools: Tools({ toolkit: agentOsToolkit }) }), []);

  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <AgentOsInterruptPayloadProvider value={interruptPayloads}>
        <RuntimeEffects
          isActive={isActive}
          onStreamingChanged={onStreamingChanged}
          onAwaitingApprovalChanged={onAwaitingApprovalChanged}
          onRunFinalized={onRunFinalized}
          onSettled={refreshHistory}
        />
        <div className="h-full min-h-0">
          <Thread
            components={{
              ToolFallback: AgentOsToolFallback,
              AssistantMessage: AgentOsAssistantMessage,
              UserMessage: AgentOsUserMessage,
            }}
            composerFooter={
              <RuntimeComposerFooter
                selectedThreadId={selectedThreadId}
                refreshKey={historyVersion}
              >
                {composerFooter}
              </RuntimeComposerFooter>
            }
          />
        </div>
      </AgentOsInterruptPayloadProvider>
    </AssistantRuntimeProvider>
  );
}

export type { AssistantThreadProps };
