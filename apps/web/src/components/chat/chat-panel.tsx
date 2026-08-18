"use client";

import { HttpAgent, type Message } from "@ag-ui/client";
import {
  ChangeEvent,
  FormEvent,
  Fragment,
  KeyboardEvent,
  TouchEvent,
  WheelEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { ApprovalPanel, type PendingInterrupt } from "@/components/chat/approval-panel";
import { AssistantMarkdown } from "@/components/chat/assistant-markdown";
import { ThinkingStepCard, type ThinkingStepState } from "@/components/chat/thinking-step-card";
import {
  summarizeToolResultContent,
  ToolCallCard,
  type ToolCallState,
} from "@/components/chat/tool-call-card";
import { formatMessageTimestamp, formatRunDurationLabel } from "@/lib/format-time";
import { isActiveRunStatus, isLikelyTransportDisconnect } from "@/lib/run-recovery";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt?: string;
  durationLabel?: string;
};

type ToolTimelineStep = ToolCallState & { kind: "tool" };

type TimelineStep = ThinkingStepState | ToolTimelineStep;

type ThreadLatestRun = {
  id: string;
  status: string;
};

type ThreadHistory = {
  thread_id: string;
  agent_id: string;
  messages: ChatMessage[];
  toolCalls: ToolCallState[];
  latestRun: ThreadLatestRun | null;
};

type UploadedArtifact = {
  artifactId: string;
  title: string;
  contentChars: number;
  mimeType: string;
};

const ARTIFACT_ID_LINE_RE =
  /(?:^|\n)\s*artifact_id\s*=\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*(?=\n|$)/gi;

function uploadContentUrl(artifactId: string): string {
  return `/api/uploads/${artifactId}/content`;
}

function isImageMimeType(mimeType: string): boolean {
  const normalized = mimeType.trim().toLowerCase();
  return (
    normalized === "image/png" ||
    normalized === "image/jpeg" ||
    normalized === "image/jpg" ||
    normalized === "image/webp"
  );
}

function mimeTypeFromFilename(filename: string): string {
  const extension = filename.toLowerCase().split(".").at(-1);
  switch (extension) {
    case "png":
      return "image/png";
    case "jpg":
    case "jpeg":
      return "image/jpeg";
    case "webp":
      return "image/webp";
    case "pdf":
      return "application/pdf";
    default:
      return "application/octet-stream";
  }
}

function parseUserMessageAttachments(content: string): {
  displayText: string;
  artifactIds: string[];
} {
  const artifactIds: string[] = [];
  const seen = new Set<string>();
  for (const match of content.matchAll(ARTIFACT_ID_LINE_RE)) {
    const id = match[1]?.toLowerCase();
    if (id !== undefined && isUuid(id) && !seen.has(id)) {
      seen.add(id);
      artifactIds.push(id);
    }
  }
  const displayText = content
    .replace(ARTIFACT_ID_LINE_RE, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return { displayText, artifactIds };
}

function buildMessageWithAttachments(text: string, artifacts: readonly UploadedArtifact[]): string {
  const lines = artifacts.map((artifact) => `artifact_id=${artifact.artifactId}`);
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return lines.join("\n");
  }
  if (lines.length === 0) {
    return trimmed;
  }
  return `${trimmed}\n\n${lines.join("\n")}`;
}

function parsePendingInterrupts(value: unknown): PendingInterrupt[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const items: PendingInterrupt[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) {
      continue;
    }
    if (
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

async function loadRunApprovalState(runId: string): Promise<{
  status: string;
  pending: PendingInterrupt[];
} | null> {
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
      status: payload.status,
      pending: parsePendingInterrupts(payload.pending_interrupts),
    };
  } catch {
    return null;
  }
}

type ChatPanelProps = {
  selectedThreadId: string | null | undefined;
  agentId: string | null;
  agentLoadError: string | null;
  /** When false, this panel stays mounted for background runs but must not own the URL/inspector. */
  isActive?: boolean;
  onRetryAgentLoad: () => void;
  onNewConversation: () => void;
  onRunStarted: (runId: string) => void;
  onStreamingChanged: (isStreaming: boolean) => void;
  onAwaitingApprovalChanged?: (isAwaiting: boolean) => void;
  onThreadChanged: (threadId: string | null, agentId?: string) => void;
  onRunFinalized: () => void;
};

const STARTER_PROMPTS = [
  "帮我梳理这个需求的目标、约束和下一步。",
  "请给出一个可执行的实施方案，并说明主要风险。",
  "请审查下面的思路，指出不成立的假设。",
];

/** Re-enable follow / hide button when this close to the bottom. */
const AUTO_SCROLL_THRESHOLD = 96;
/** Only show「回到最新」after the user has clearly left the live edge. */
const SHOW_SCROLL_TO_LATEST_THRESHOLD = 180;
const MAX_COMPOSER_HEIGHT = 200;
const MAX_UPLOAD_FILES = 3;
/** Stream-loss recovery poll: 1s → 2s → 5s, then capped at 10s. */
const RECOVERY_POLL_DELAYS_MS = [1_000, 2_000, 5_000] as const;
const RECOVERY_POLL_MAX_DELAY_MS = 10_000;
const RECOVERY_POLL_MAX_ATTEMPTS = 60;
const UPLOAD_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp";
const SUPPORTED_UPLOAD_EXTENSIONS = new Set(["pdf", "png", "jpg", "jpeg", "webp"]);

function isUuid(value: string): boolean {
  const parts = value.split("-");

  return (
    parts.length === 5 &&
    parts[0].length === 8 &&
    parts[1].length === 4 &&
    parts[2].length === 4 &&
    parts[3].length === 4 &&
    parts[4].length === 12 &&
    parts.every((part) => /^[0-9a-f]+$/i.test(part))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSupportedUpload(file: File): boolean {
  const extension = file.name.toLowerCase().split(".").at(-1);
  return extension !== undefined && SUPPORTED_UPLOAD_EXTENSIONS.has(extension);
}

function parseUploadedArtifact(value: unknown): UploadedArtifact | null {
  if (
    !isRecord(value) ||
    typeof value.artifact_id !== "string" ||
    !isUuid(value.artifact_id) ||
    typeof value.title !== "string" ||
    typeof value.content_chars !== "number"
  ) {
    return null;
  }

  const mimeType =
    typeof value.mime_type === "string" && value.mime_type.trim()
      ? value.mime_type.trim().toLowerCase()
      : "application/octet-stream";

  return {
    artifactId: value.artifact_id,
    title: value.title,
    contentChars: value.content_chars,
    mimeType,
  };
}

function parseHistoryToolCalls(value: unknown): ToolCallState[] | null {
  if (value === undefined) {
    return [];
  }

  if (!Array.isArray(value)) {
    return null;
  }

  const toolCalls: ToolCallState[] = [];

  for (const item of value) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.tool_name !== "string" ||
      !isRecord(item.args) ||
      (item.status !== "done" && item.status !== "error") ||
      typeof item.summary !== "string" ||
      typeof item.after_message_id !== "string" ||
      (item.provider !== undefined && item.provider !== null && typeof item.provider !== "string")
    ) {
      return null;
    }

    toolCalls.push({
      id: item.id,
      toolName: item.tool_name,
      argsText: JSON.stringify(item.args),
      status: item.status,
      resultSummary: item.summary,
      provider: typeof item.provider === "string" ? item.provider : undefined,
      expanded: false,
      afterMessageId: item.after_message_id,
    });
  }

  return toolCalls;
}

function parseThreadHistory(value: unknown): ThreadHistory | null {
  if (
    !isRecord(value) ||
    typeof value.thread_id !== "string" ||
    !isUuid(value.thread_id) ||
    typeof value.agent_id !== "string" ||
    !isUuid(value.agent_id) ||
    !Array.isArray(value.messages)
  ) {
    return null;
  }

  const messages: ChatMessage[] = [];

  for (const message of value.messages) {
    if (
      !isRecord(message) ||
      typeof message.id !== "string" ||
      (message.role !== "user" && message.role !== "assistant") ||
      typeof message.content !== "string" ||
      typeof message.created_at !== "string"
    ) {
      return null;
    }

    messages.push({
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
    });
  }

  const toolCalls = parseHistoryToolCalls(value.tool_calls);
  if (toolCalls === null) {
    return null;
  }

  let latestRun: ThreadLatestRun | null = null;
  if (value.latest_run !== undefined && value.latest_run !== null) {
    if (
      !isRecord(value.latest_run) ||
      typeof value.latest_run.id !== "string" ||
      !isUuid(value.latest_run.id) ||
      typeof value.latest_run.status !== "string"
    ) {
      return null;
    }
    latestRun = { id: value.latest_run.id, status: value.latest_run.status };
  }

  return {
    thread_id: value.thread_id,
    agent_id: value.agent_id,
    messages,
    toolCalls,
    latestRun,
  };
}

function toAgentMessages(messages: ChatMessage[]): Message[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
  }));
}

function toDisplayMessages(
  messages: readonly Message[],
  previous: ChatMessage[] = [],
): ChatMessage[] {
  const previousById = new Map(previous.map((message) => [message.id, message]));
  const displayMessages: ChatMessage[] = [];

  for (const message of messages) {
    if (message.role === "tool") {
      continue;
    }

    if (
      (message.role !== "user" && message.role !== "assistant") ||
      typeof message.content !== "string"
    ) {
      continue;
    }

    const hasToolCalls =
      "toolCalls" in message && Array.isArray(message.toolCalls) && message.toolCalls.length > 0;

    if (message.role === "assistant" && !message.content && hasToolCalls) {
      continue;
    }

    const prior = previousById.get(message.id);

    displayMessages.push({
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: prior?.createdAt,
      durationLabel: prior?.durationLabel,
    });
  }

  return displayMessages;
}

function upsertTimelineStep(current: TimelineStep[], next: TimelineStep): TimelineStep[] {
  const index = current.findIndex((item) => item.id === next.id);
  if (index === -1) {
    return [...current, next];
  }

  const updated = [...current];
  updated[index] = next;
  return updated;
}

function updateLatestThinkingStep(
  current: TimelineStep[],
  afterMessageId: string,
  update: Partial<ThinkingStepState>,
): TimelineStep[] {
  for (let index = current.length - 1; index >= 0; index -= 1) {
    const step = current[index];
    if (step?.kind === "thinking" && step.afterMessageId === afterMessageId) {
      const next = [...current];
      next[index] = { ...step, ...update };
      return next;
    }
  }

  return current;
}

function toolStatesFromTimeline(steps: TimelineStep[]): ToolCallState[] {
  return steps
    .filter((step): step is ToolTimelineStep => step.kind === "tool")
    .map((step) => ({
      id: step.id,
      toolName: step.toolName,
      argsText: step.argsText,
      status: step.status,
      resultSummary: step.resultSummary,
      provider: step.provider,
      expanded: false,
      afterMessageId: step.afterMessageId,
    }));
}

function mergeHistoryToolCalls(
  current: ToolCallState[],
  incoming: ToolCallState[],
): ToolCallState[] {
  const merged = [...current];
  const seen = new Set(current.map((item) => item.id));

  for (const item of incoming) {
    if (seen.has(item.id)) {
      continue;
    }

    merged.push({ ...item, expanded: false });
    seen.add(item.id);
  }

  return merged;
}

function createAgent(threadId: string, messages: ChatMessage[], agentId: string | null): HttpAgent {
  return new HttpAgent({
    url: "/api/ag-ui/runs",
    threadId,
    initialMessages: toAgentMessages(messages),
    headers: agentId === null ? {} : { "X-AgentOS-Agent-Id": agentId },
  });
}

function updateThreadInUrl(threadId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("thread", threadId);
  window.history.replaceState(window.history.state, "", url);
}

function agentErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.includes("409")) {
    return "当前会话仍在处理中，请等待完成或停止当前操作。";
  }

  return "助手处理失败，请稍后重试。";
}

async function fetchRunStatus(runId: string): Promise<string | null> {
  try {
    const response = await fetch(`/api/runs/${runId}`, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    const payload: unknown = await response.json();
    if (!isRecord(payload) || typeof payload.status !== "string") {
      return null;
    }
    return payload.status;
  } catch {
    return null;
  }
}

async function loadRunDurationLabel(runId: string): Promise<string | null> {
  try {
    const response = await fetch(`/api/runs/${runId}`, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }

    const payload: unknown = await response.json();
    if (
      !isRecord(payload) ||
      typeof payload.created_at !== "string" ||
      (payload.started_at !== null && typeof payload.started_at !== "string") ||
      (payload.completed_at !== null && typeof payload.completed_at !== "string") ||
      typeof payload.status !== "string"
    ) {
      return null;
    }

    return formatRunDurationLabel(
      typeof payload.started_at === "string" ? payload.started_at : null,
      typeof payload.completed_at === "string" ? payload.completed_at : null,
      payload.created_at,
      payload.status,
    );
  } catch {
    return null;
  }
}

export function ChatPanel({
  selectedThreadId,
  agentId,
  agentLoadError,
  isActive = true,
  onRetryAgentLoad,
  onNewConversation,
  onRunStarted,
  onStreamingChanged,
  onAwaitingApprovalChanged,
  onThreadChanged,
  onRunFinalized,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyLoadFailed, setHistoryLoadFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [showScrollToLatest, setShowScrollToLatest] = useState(false);
  const [timelineSteps, setTimelineSteps] = useState<TimelineStep[]>([]);
  const [historyToolCalls, setHistoryToolCalls] = useState<ToolCallState[]>([]);
  const [liveUserMessageId, setLiveUserMessageId] = useState<string | null>(null);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [approvalRunId, setApprovalRunId] = useState<string | null>(null);
  const [pendingInterrupts, setPendingInterrupts] = useState<PendingInterrupt[]>([]);
  const [uploadedArtifacts, setUploadedArtifacts] = useState<UploadedArtifact[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);

  const agentRef = useRef<HttpAgent | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const lastRunIdRef = useRef<string | null>(null);
  const lastRunThreadIdRef = useRef<string | null>(null);
  const cancellationRequestedRef = useRef(false);
  const recoveryInFlightRef = useRef(false);
  const deferredStreamErrorRef = useRef<string | null>(null);
  // Follow new tokens only while the user stays near the bottom.
  const autoScrollRef = useRef(true);
  // Ignore scroll events caused by our own scrollTo(bottom) calls.
  const programmaticScrollRef = useRef(false);
  const touchStartYRef = useRef<number | null>(null);
  const isActiveRef = useRef(isActive);
  const initialThreadIdRef = useRef<string | null | undefined>(undefined);
  const messagesViewportRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  isActiveRef.current = isActive;

  if (agentRef.current === null) {
    agentRef.current = createAgent("new", [], agentId);
  }

  useEffect(() => {
    onStreamingChanged(isStreaming);
  }, [isStreaming, onStreamingChanged]);

  useEffect(() => {
    onAwaitingApprovalChanged?.(pendingInterrupts.length > 0);
  }, [pendingInterrupts, onAwaitingApprovalChanged]);

  useEffect(() => {
    if (uploadNotice === null || isUploading) {
      return;
    }

    const timeout = window.setTimeout(() => setUploadNotice(null), 4_000);
    return () => window.clearTimeout(timeout);
  }, [isUploading, uploadNotice]);

  const clearApprovalState = useCallback(() => {
    setApprovalRunId(null);
    setPendingInterrupts([]);
  }, []);

  const applyApprovalStateFromRun = useCallback(
    async (runId: string) => {
      const state = await loadRunApprovalState(runId);
      if (state === null) {
        return;
      }
      if (state.status === "waiting_approval" && state.pending.length > 0) {
        setApprovalRunId(runId);
        setPendingInterrupts(state.pending);
        return;
      }
      clearApprovalState();
    },
    [clearApprovalState],
  );

  async function handleApprovalResolved() {
    const runId = approvalRunId;
    clearApprovalState();
    if (runId === null) {
      return;
    }

    setIsStreaming(true);
    setError(null);
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const state = await loadRunApprovalState(runId);
      if (state === null) {
        break;
      }
      if (state.status === "waiting_approval" && state.pending.length > 0) {
        setApprovalRunId(runId);
        setPendingInterrupts(state.pending);
        setIsStreaming(false);
        return;
      }
      if (
        state.status === "completed" ||
        state.status === "failed" ||
        state.status === "cancelled"
      ) {
        break;
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 500));
    }
    setIsStreaming(false);
    setHistoryRefreshKey((current) => current + 1);
    onRunFinalized();
  }

  useEffect(() => {
    if (!isActive) {
      return;
    }

    if (threadId !== null) {
      updateThreadInUrl(threadId);
      return;
    }

    if (selectedThreadId === null) {
      const url = new URL(window.location.href);
      url.searchParams.delete("thread");
      window.history.replaceState(window.history.state, "", url);
    }
  }, [isActive, selectedThreadId, threadId]);

  useEffect(() => {
    if (autoScrollRef.current) {
      scrollMessagesToBottom();
    }
  }, [messages, timelineSteps]);

  useEffect(() => {
    const textarea = textareaRef.current;

    if (textarea === null) {
      return;
    }

    textarea.style.height = "0px";

    const needsInternalScroll = textarea.scrollHeight > MAX_COMPOSER_HEIGHT;
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
    textarea.style.overflowY = needsInternalScroll ? "auto" : "hidden";
  }, [draft]);

  useEffect(() => {
    return () => {
      agentRef.current?.abortRun();
    };
  }, []);

  useEffect(() => {
    if (threadId === null && !isStreaming) {
      agentRef.current = createAgent("new", messages, agentId);
    }
  }, [agentId, isStreaming, messages, threadId]);

  const settleRunAfterStreamLoss = useCallback(
    async (runId: string) => {
      if (recoveryInFlightRef.current) {
        return;
      }
      recoveryInFlightRef.current = true;
      deferredStreamErrorRef.current = null;
      setError(null);
      setIsStreaming(true);
      activeRunIdRef.current = runId;
      lastRunIdRef.current = runId;

      try {
        for (let attempt = 0; attempt < RECOVERY_POLL_MAX_ATTEMPTS; attempt += 1) {
          if (cancellationRequestedRef.current) {
            break;
          }

          const status = await fetchRunStatus(runId);
          if (status === null) {
            await new Promise<void>((resolve) => window.setTimeout(resolve, 1_000));
            continue;
          }

          if (status === "waiting_approval") {
            await applyApprovalStateFromRun(runId);
            setHistoryRefreshKey((current) => current + 1);
            setIsStreaming(false);
            onRunFinalized();
            return;
          }

          if (!isActiveRunStatus(status)) {
            clearApprovalState();
            setHistoryRefreshKey((current) => current + 1);
            const durationLabel = await loadRunDurationLabel(runId);
            if (durationLabel !== null) {
              setMessages((previous) => {
                const latestUserIndex = previous.reduce(
                  (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
                  -1,
                );
                const assistantIndex = previous.findIndex(
                  (message, index) => index > latestUserIndex && message.role === "assistant",
                );
                if (assistantIndex === -1) {
                  return previous;
                }
                return previous.map((message, index) =>
                  index === assistantIndex ? { ...message, durationLabel } : message,
                );
              });
            }
            setIsStreaming(false);
            onRunFinalized();
            return;
          }

          const delay = RECOVERY_POLL_DELAYS_MS[attempt] ?? RECOVERY_POLL_MAX_DELAY_MS;
          await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
        }

        // Timed out waiting; leave a soft message only if still active.
        const status = await fetchRunStatus(runId);
        if (isActiveRunStatus(status)) {
          setError("仍在后台生成，可稍后再打开本会话查看结果。");
        } else {
          setHistoryRefreshKey((current) => current + 1);
        }
        setIsStreaming(false);
        onRunFinalized();
      } finally {
        recoveryInFlightRef.current = false;
        if (activeRunIdRef.current === runId) {
          activeRunIdRef.current = null;
        }
      }
    },
    [applyApprovalStateFromRun, clearApprovalState, onRunFinalized],
  );

  useEffect(() => {
    if (threadId === null) {
      return;
    }

    let cancelled = false;

    async function refreshAfterForeground() {
      const runId = lastRunIdRef.current;
      if (
        runId === null ||
        lastRunThreadIdRef.current !== threadId ||
        cancelled ||
        recoveryInFlightRef.current
      ) {
        return;
      }

      const status = await fetchRunStatus(runId);
      if (cancelled || status === null) {
        return;
      }

      if (isActiveRunStatus(status) || status === "waiting_approval") {
        // SSE may already be dead while the server run continues — resync quietly.
        void settleRunAfterStreamLoss(runId);
        return;
      }

      // Terminal run after a backgrounded tab: drop stale red errors and refresh.
      setError(null);
      clearApprovalState();
      setHistoryRefreshKey((current) => current + 1);
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refreshAfterForeground();
      }
    };

    const handleOnline = () => {
      void refreshAfterForeground();
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("online", handleOnline);
    if (document.visibilityState === "visible") {
      void refreshAfterForeground();
    }

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("online", handleOnline);
    };
  }, [clearApprovalState, settleRunAfterStreamLoss, threadId]);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    const initialThreadId =
      initialThreadIdRef.current === undefined
        ? new URL(window.location.href).searchParams.get("thread")
        : initialThreadIdRef.current;

    initialThreadIdRef.current = initialThreadId;

    const requestedThreadId = selectedThreadId === undefined ? initialThreadId : selectedThreadId;

    if (requestedThreadId === null || (requestedThreadId === threadId && historyRefreshKey === 0)) {
      setIsLoadingHistory(false);
      return () => controller.abort();
    }

    if (!isUuid(requestedThreadId)) {
      setHistoryLoadFailed(true);
      setError("会话链接无效。请新建对话后再继续。");
      setIsLoadingHistory(false);
      return () => controller.abort();
    }

    setIsLoadingHistory(true);
    setHistoryLoadFailed(false);

    void (async () => {
      try {
        const response = await fetch(`/api/threads/${requestedThreadId}/messages`, {
          cache: "no-store",
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(response.status === 404 ? "找不到该会话。" : "无法读取会话历史。");
        }

        const history = parseThreadHistory((await response.json()) as unknown);

        if (history === null || history.thread_id !== requestedThreadId) {
          throw new Error("会话历史格式无效。");
        }

        if (isCurrent) {
          if (history.thread_id !== threadId) {
            setUploadedArtifacts([]);
            setUploadNotice(null);
          }
          agentRef.current = createAgent(history.thread_id, history.messages, agentId);
          setMessages(history.messages);
          setThreadId(history.thread_id);
          setTimelineSteps([]);
          setHistoryToolCalls(history.toolCalls);
          setLiveUserMessageId(null);
          setHistoryRefreshKey(0);
          setError(null);
          if (isActiveRef.current) {
            updateThreadInUrl(history.thread_id);
          }
          onThreadChanged(history.thread_id, history.agent_id);

          // Full page reload clears in-memory run refs; resume from durable latest_run.
          const latest = history.latestRun;
          if (latest !== null) {
            lastRunIdRef.current = latest.id;
            lastRunThreadIdRef.current = history.thread_id;
            if (isActiveRunStatus(latest.status)) {
              onRunStarted(latest.id);
              void settleRunAfterStreamLoss(latest.id);
            } else {
              const lastMessage = history.messages.at(-1);
              if (lastMessage?.role === "user" && latest.status === "failed") {
                setError("上次生成失败或中断，可重新发送。");
              } else if (lastMessage?.role === "user" && latest.status === "cancelled") {
                setError("上次生成已取消，可重新发送。");
              }
            }
          }
        }
      } catch (caughtError: unknown) {
        if (isCurrent && !controller.signal.aborted) {
          setHistoryLoadFailed(true);
          setError(caughtError instanceof Error ? caughtError.message : "无法读取会话历史。");
        }
      } finally {
        if (isCurrent) {
          setIsLoadingHistory(false);
        }
      }
    })();

    return () => {
      isCurrent = false;
      controller.abort();
    };
  }, [
    agentId,
    historyRefreshKey,
    onRunStarted,
    onThreadChanged,
    selectedThreadId,
    settleRunAfterStreamLoss,
    threadId,
  ]);

  function scrollMessagesToBottom(behavior: ScrollBehavior = "auto") {
    const viewport = messagesViewportRef.current;

    if (viewport === null) {
      return;
    }

    programmaticScrollRef.current = true;
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior,
    });
    // Clear after the browser applies the scroll so onScroll does not treat it as user intent.
    window.requestAnimationFrame(() => {
      programmaticScrollRef.current = false;
    });
  }

  function stopStreaming() {
    cancellationRequestedRef.current = true;
    const runId = activeRunIdRef.current;
    if (runId !== null) {
      void fetch(`/api/runs/${runId}/cancel`, {
        method: "POST",
        cache: "no-store",
        keepalive: true,
      }).catch(() => undefined);
    }
    agentRef.current?.abortRun();
  }

  function scrollToLatest() {
    autoScrollRef.current = true;
    setShowScrollToLatest(false);
    scrollMessagesToBottom("smooth");
  }

  function pauseAutoScroll() {
    // User is reading history; do not yank them back when new tokens arrive.
    // Button visibility is owned by handleMessageScroll (actual distance from bottom).
    autoScrollRef.current = false;
  }

  function isNestedScrollTarget(target: EventTarget | null): boolean {
    return (
      target instanceof Element &&
      Boolean(target.closest(".agentos-code-block-scroll, .agentos-reasoning-content"))
    );
  }

  function handleViewportWheel(event: WheelEvent<HTMLDivElement>) {
    // Nested panes own their wheel; "回到最新" is session-viewport only.
    if (isNestedScrollTarget(event.target)) {
      return;
    }

    // deltaY < 0 means scrolling up toward older messages.
    if (event.deltaY < 0) {
      pauseAutoScroll();
    }
  }

  function handleViewportTouchStart(event: TouchEvent<HTMLDivElement>) {
    if (isNestedScrollTarget(event.target)) {
      touchStartYRef.current = null;
      return;
    }
    touchStartYRef.current = event.touches[0]?.clientY ?? null;
  }

  function handleViewportTouchMove(event: TouchEvent<HTMLDivElement>) {
    if (isNestedScrollTarget(event.target) || touchStartYRef.current === null) {
      return;
    }

    const startY = touchStartYRef.current;
    const currentY = event.touches[0]?.clientY;
    // Finger moved down → content scrolls up → user is leaving the live edge.
    if (currentY !== undefined && currentY - startY > 10) {
      pauseAutoScroll();
    }
  }

  function handleMessageScroll() {
    if (programmaticScrollRef.current) {
      return;
    }

    const viewport = messagesViewportRef.current;

    if (viewport === null) {
      return;
    }

    // Session viewport only. Content growth while following must not flash「回到最新」
    // or cancel follow before the stick-to-bottom effect runs.
    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    const nearBottom = distanceFromBottom < AUTO_SCROLL_THRESHOLD;

    if (nearBottom) {
      autoScrollRef.current = true;
      setShowScrollToLatest(false);
      return;
    }

    if (autoScrollRef.current) {
      // Still in follow mode (likely new tokens grew the list). Keep button hidden;
      // the messages effect will pin us back to the bottom.
      setShowScrollToLatest(false);
      return;
    }

    setShowScrollToLatest(distanceFromBottom > SHOW_SCROLL_TO_LATEST_THRESHOLD);
  }

  async function copyAssistantMessage(message: ChatMessage) {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
      window.setTimeout(() => setCopiedMessageId(null), 1_500);
    } catch {
      setError("无法复制回复，请检查浏览器权限。");
    }
  }

  function applyStarterPrompt(prompt: string) {
    setDraft(prompt);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function sendMessage(contentOverride?: string) {
    const pending = uploadedArtifacts;
    const rawText = contentOverride ?? draft;
    const content =
      contentOverride !== undefined
        ? contentOverride.trim()
        : buildMessageWithAttachments(rawText, pending);

    if (!content || isStreaming || isLoadingHistory || historyLoadFailed) {
      return;
    }

    const agent = agentRef.current;
    if (agent === null) {
      setError("助手尚未准备好，请稍后重试。");
      return;
    }

    const userMessageId = crypto.randomUUID();
    const sentAt = new Date().toISOString();

    agent.addMessage({
      id: userMessageId,
      role: "user",
      content,
    });

    setMessages((previous) => {
      const next = toDisplayMessages(agent.messages, previous);
      return next.map((message) =>
        message.id === userMessageId ? { ...message, createdAt: sentAt } : message,
      );
    });
    if (contentOverride === undefined) {
      setDraft("");
      setUploadedArtifacts([]);
      setUploadNotice(null);
    }
    setError(null);
    setHistoryToolCalls((current) =>
      mergeHistoryToolCalls(current, toolStatesFromTimeline(timelineSteps)),
    );
    setTimelineSteps([]);
    setLiveUserMessageId(userMessageId);
    setIsStreaming(true);
    autoScrollRef.current = true;
    setShowScrollToLatest(false);
    cancellationRequestedRef.current = false;

    let activeRunId: string | null = null;
    let recoverRunId: string | null = null;
    deferredStreamErrorRef.current = null;

    try {
      await agent.runAgent(undefined, {
        onMessagesChanged: ({ messages: nextMessages }) => {
          setMessages((previous) => toDisplayMessages(nextMessages, previous));
        },
        onRunStartedEvent: ({ event, agent: runningAgent }) => {
          if (isUuid(event.runId)) {
            activeRunId = event.runId;
            activeRunIdRef.current = event.runId;
            lastRunIdRef.current = event.runId;
            lastRunThreadIdRef.current = isUuid(event.threadId) ? event.threadId : null;
            onRunStarted(event.runId);
          }

          if (isUuid(event.threadId)) {
            runningAgent.threadId = event.threadId;
            setThreadId(event.threadId);
            onThreadChanged(event.threadId);
            if (isActiveRef.current) {
              updateThreadInUrl(event.threadId);
            }
          }
        },
        onRunErrorEvent: ({ event }) => {
          // Mobile background / SSE drop often emits RunError while the server keeps going.
          // Defer hard UI failure until we probe Run status in the catch path.
          deferredStreamErrorRef.current = event.message || "助手处理失败，请稍后重试。";
        },
        onReasoningStartEvent: ({ event }) => {
          setTimelineSteps((current) =>
            upsertTimelineStep(current, {
              kind: "thinking",
              id: event.messageId,
              content: "正在理解问题与相关上下文",
              status: "running",
              expanded: false,
              afterMessageId: userMessageId,
            }),
          );
        },
        onReasoningMessageStartEvent: () => {
          setTimelineSteps((current) =>
            updateLatestThinkingStep(current, userMessageId, {
              content: "正在拆解任务目标",
            }),
          );
        },
        onReasoningMessageContentEvent: ({ reasoningMessageBuffer }) => {
          if (!reasoningMessageBuffer.trim()) {
            return;
          }

          setTimelineSteps((current) =>
            updateLatestThinkingStep(current, userMessageId, {
              content: "正在整理分析结果",
            }),
          );
        },
        onReasoningMessageEndEvent: () => {
          setTimelineSteps((current) =>
            updateLatestThinkingStep(current, userMessageId, {
              content: "分析完成，正在准备下一步",
            }),
          );
        },
        onReasoningEndEvent: ({ event }) => {
          setTimelineSteps((current) =>
            current.map((step) =>
              step.kind === "thinking" && step.id === event.messageId
                ? {
                    ...step,
                    status: "done",
                    content: step.content || "已完成这一步处理",
                    expanded: false,
                  }
                : step,
            ),
          );
        },
        onToolCallStartEvent: ({ event }) => {
          setTimelineSteps((current) => {
            const next = updateLatestThinkingStep(current, userMessageId, {
              content: "正在调用相关能力",
            });
            return upsertTimelineStep(next, {
              kind: "tool",
              id: event.toolCallId,
              toolName: event.toolCallName,
              argsText: "",
              status: "running",
              expanded: false,
              afterMessageId: userMessageId,
            });
          });
        },
        onToolCallArgsEvent: ({ event, toolCallBuffer, toolCallName }) => {
          setTimelineSteps((current) => {
            const existing = current.find(
              (step): step is ToolTimelineStep =>
                step.kind === "tool" && step.id === event.toolCallId,
            );
            return upsertTimelineStep(current, {
              kind: "tool",
              id: event.toolCallId,
              toolName: toolCallName || existing?.toolName || "tool",
              argsText: toolCallBuffer,
              status: existing?.status ?? "running",
              resultSummary: existing?.resultSummary,
              provider: existing?.provider,
              expanded: existing?.expanded ?? false,
              afterMessageId: existing?.afterMessageId ?? userMessageId,
            });
          });
        },
        onToolCallEndEvent: ({ event, toolCallName, toolCallArgs }) => {
          setTimelineSteps((current) => {
            const existing = current.find(
              (step): step is ToolTimelineStep =>
                step.kind === "tool" && step.id === event.toolCallId,
            );
            return upsertTimelineStep(current, {
              kind: "tool",
              id: event.toolCallId,
              toolName: toolCallName || existing?.toolName || "tool",
              argsText: existing?.argsText || JSON.stringify(toolCallArgs ?? {}),
              status: existing?.status ?? "running",
              resultSummary: existing?.resultSummary,
              provider: existing?.provider,
              expanded: existing?.expanded ?? false,
              afterMessageId: existing?.afterMessageId ?? userMessageId,
            });
          });
        },
        onToolCallResultEvent: ({ event }) => {
          const summarized = summarizeToolResultContent(event.content);
          setTimelineSteps((current) => {
            const existing = current.find(
              (step): step is ToolTimelineStep =>
                step.kind === "tool" && step.id === event.toolCallId,
            );
            return upsertTimelineStep(current, {
              kind: "tool",
              id: event.toolCallId,
              toolName: existing?.toolName || "tool",
              argsText: existing?.argsText || "",
              status: summarized.status,
              resultSummary: summarized.summary,
              provider: summarized.provider ?? existing?.provider,
              expanded: false,
              afterMessageId: existing?.afterMessageId ?? userMessageId,
            });
          });
        },
      });

      const completedAt = new Date().toISOString();
      setMessages((previous) => {
        const latestUserIndex = previous.reduce(
          (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
          -1,
        );
        const assistantIndex = previous.findIndex(
          (message, index) => index > latestUserIndex && message.role === "assistant",
        );

        if (assistantIndex === -1) {
          return previous;
        }

        return previous.map((message, index) =>
          index === assistantIndex
            ? { ...message, createdAt: message.createdAt ?? completedAt }
            : message,
        );
      });

      if (activeRunId !== null) {
        const durationLabel = await loadRunDurationLabel(activeRunId);
        if (durationLabel !== null) {
          setMessages((previous) => {
            const latestUserIndex = previous.reduce(
              (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
              -1,
            );
            const assistantIndex = previous.findIndex(
              (message, index) => index > latestUserIndex && message.role === "assistant",
            );

            if (assistantIndex === -1) {
              return previous;
            }

            return previous.map((message, index) =>
              index === assistantIndex ? { ...message, durationLabel } : message,
            );
          });
        }
      }
    } catch (caughtError: unknown) {
      if (cancellationRequestedRef.current) {
        if (activeRunId !== null) {
          const durationLabel = await loadRunDurationLabel(activeRunId);
          if (durationLabel !== null) {
            setMessages((previous) => {
              const latestUserIndex = previous.reduce(
                (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
                -1,
              );
              const assistantIndex = previous.findIndex(
                (message, index) => index > latestUserIndex && message.role === "assistant",
              );

              if (assistantIndex === -1) {
                return previous;
              }

              return previous.map((message, index) =>
                index === assistantIndex ? { ...message, durationLabel } : message,
              );
            });
          }
        }
      } else if (activeRunId !== null) {
        const status = await fetchRunStatus(activeRunId);
        if (isActiveRunStatus(status)) {
          // Browser SSE died; server run is still live — quiet poll + history refresh.
          recoverRunId = activeRunId;
          setError(null);
        } else if (status === "completed" || status === "cancelled") {
          setError(null);
          setHistoryRefreshKey((current) => current + 1);
        } else if (status === null || isLikelyTransportDisconnect(caughtError)) {
          // Cannot reach API or looks like a transport drop — keep trying quietly.
          recoverRunId = activeRunId;
          setError(null);
        } else {
          setError(deferredStreamErrorRef.current ?? agentErrorMessage(caughtError));
        }
      } else {
        setError(deferredStreamErrorRef.current ?? agentErrorMessage(caughtError));
      }
    } finally {
      const finishedRunId = activeRunId;
      const wasCancelled = cancellationRequestedRef.current;
      deferredStreamErrorRef.current = null;

      if (recoverRunId !== null && !wasCancelled) {
        cancellationRequestedRef.current = false;
        void settleRunAfterStreamLoss(recoverRunId);
        return;
      }

      activeRunIdRef.current = null;
      cancellationRequestedRef.current = false;
      setIsStreaming(false);
      if (finishedRunId !== null && !wasCancelled) {
        await applyApprovalStateFromRun(finishedRunId);
      } else {
        clearApprovalState();
      }
      onRunFinalized();
    }
  }

  async function ensureThreadForUpload(): Promise<string | null> {
    if (threadId !== null) {
      return threadId;
    }

    try {
      const response = await fetch("/api/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(agentId === null ? {} : { agent_id: agentId }),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (
        !response.ok ||
        !isRecord(payload) ||
        typeof payload.id !== "string" ||
        !isUuid(payload.id)
      ) {
        setError("无法创建会话，请稍后重试后再上传文件。");
        return null;
      }

      const createdId = payload.id;
      const createdAgentId =
        typeof payload.agent_id === "string" && isUuid(payload.agent_id)
          ? payload.agent_id
          : agentId;

      agentRef.current = createAgent(createdId, messages, createdAgentId);
      setThreadId(createdId);
      updateThreadInUrl(createdId);
      onThreadChanged(createdId, createdAgentId ?? undefined);
      return createdId;
    } catch {
      setError("无法创建会话，请检查网络后重试。");
      return null;
    }
  }

  async function handleFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = "";

    if (files.length === 0) {
      return;
    }

    const activeThreadId = await ensureThreadForUpload();
    if (activeThreadId === null) {
      return;
    }

    const remainingSlots = MAX_UPLOAD_FILES - uploadedArtifacts.length;
    if (files.length > remainingSlots) {
      setError(`每个会话最多上传 ${MAX_UPLOAD_FILES} 个附件，当前还可上传 ${remainingSlots} 个。`);
      return;
    }

    const unsupported = files.find((file) => !isSupportedUpload(file));
    if (unsupported !== undefined) {
      setError(`不支持“${unsupported.name}”，请选择 PDF、PNG、JPG 或 WebP 文件。`);
      return;
    }

    setIsUploading(true);
    setUploadNotice(`正在上传 ${files.length} 个文件…`);
    setError(null);

    const uploaded: UploadedArtifact[] = [];
    const failedNames: string[] = [];

    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("thread_id", activeThreadId);

        try {
          const response = await fetch("/api/uploads", {
            method: "POST",
            body: formData,
          });
          const payload: unknown = await response.json();
          let artifact = response.ok ? parseUploadedArtifact(payload) : null;
          if (artifact !== null && artifact.mimeType === "application/octet-stream") {
            artifact = {
              ...artifact,
              mimeType: mimeTypeFromFilename(file.name),
            };
          }

          if (artifact === null) {
            failedNames.push(file.name);
            continue;
          }

          uploaded.push(artifact);
        } catch {
          failedNames.push(file.name);
        }
      }

      if (uploaded.length > 0) {
        setUploadedArtifacts((current) => [...current, ...uploaded]);
        setUploadNotice(
          failedNames.length === 0
            ? `已添加 ${uploaded.length} 个附件，可输入说明后发送，或直接发送。`
            : `已添加 ${uploaded.length} 个附件；部分文件失败。`,
        );
      } else {
        setUploadNotice(null);
      }

      if (failedNames.length > 0) {
        setError(`以下文件上传失败：${failedNames.join("、")}。请检查格式或文件大小后重试。`);
      }
    } finally {
      setIsUploading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isStreaming) {
      stopStreaming();
      return;
    }

    void sendMessage();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void sendMessage();
    }
  }

  function startNewConversation() {
    if (!isLoadingHistory) {
      setUploadedArtifacts([]);
      setUploadNotice(null);
      onNewConversation();
    }
  }

  function toggleTimelineStep(stepId: string) {
    setTimelineSteps((current) =>
      current.map((step) => (step.id === stepId ? { ...step, expanded: !step.expanded } : step)),
    );
  }

  function toggleHistoryToolCall(toolCallId: string) {
    setHistoryToolCalls((current) =>
      current.map((item) =>
        item.id === toolCallId ? { ...item, expanded: !item.expanded } : item,
      ),
    );
  }

  const latestUserMessageIndex = messages.reduce(
    (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
    -1,
  );

  const currentAssistantMessageIndex = messages.findIndex(
    (message, index) => index > latestUserMessageIndex && message.role === "assistant",
  );

  const statusLabel = isLoadingHistory
    ? "正在读取会话"
    : pendingInterrupts.length > 0
      ? "等待确认"
      : isStreaming
        ? "正在处理"
        : "助手已就绪";

  return (
    <section
      className={`agentos-chat-panel flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden ${
        isStreaming ? "agentos-is-streaming" : ""
      }`}
    >
      <header className="agentos-chat-header">
        <div className="agentos-thread-heading">
          <p className="agentos-thread-kicker">通用助手</p>
          <div className="agentos-thread-title-row">
            <h1 className="agentos-chat-heading">{threadId === null ? "新建会话" : "当前会话"}</h1>
            <p aria-live="polite" className="agentos-chat-status">
              <span aria-hidden="true" />
              {statusLabel}
            </p>
          </div>
          <p className="agentos-chat-subheading">
            {threadId === null ? "准备开始新的任务" : "已恢复这个会话"}
          </p>
        </div>

        <div className="agentos-chat-header-actions">
          <button
            type="button"
            onClick={startNewConversation}
            disabled={isLoadingHistory}
            className="agentos-new-chat-button disabled:cursor-not-allowed disabled:opacity-40"
          >
            <span aria-hidden="true">＋</span>
            新建对话
          </button>
        </div>
      </header>

      {agentLoadError !== null ? (
        <div role="alert" className="agentos-chat-error border-b px-5 py-3 text-sm">
          无法加载助手列表，将使用默认助手继续对话。
          <button
            type="button"
            onClick={onRetryAgentLoad}
            className="ml-3 underline underline-offset-2"
          >
            重试
          </button>
        </div>
      ) : null}

      <div
        ref={messagesViewportRef}
        onScroll={handleMessageScroll}
        onWheel={handleViewportWheel}
        onTouchStart={handleViewportTouchStart}
        onTouchMove={handleViewportTouchMove}
        className="agentos-message-viewport min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div className="agentos-message-list mx-auto">
          {messages.length === 0 ? (
            <div className="agentos-empty-state">
              <p className="agentos-empty-eyebrow">Start with a task</p>
              <p className="agentos-chat-heading">从一个任务开始</p>
              <p className="agentos-chat-subheading">
                助手会实时展示处理进度、相关资料和最终回答。
              </p>
              <div className="agentos-starter-prompts">
                {STARTER_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => applyStarterPrompt(prompt)}
                    className="agentos-starter-prompt max-w-full text-left text-sm"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message, index) => {
              // Process steps are keyed to the user message that started the run; render
              // them inside the first following assistant bubble only.
              const precedingUserIndex =
                message.role === "assistant"
                  ? (() => {
                      for (let i = index - 1; i >= 0; i -= 1) {
                        if (messages[i]?.role === "user") {
                          return i;
                        }
                      }
                      return -1;
                    })()
                  : -1;
              const precedingUserId =
                precedingUserIndex >= 0 ? messages[precedingUserIndex]?.id : undefined;
              const isPrimaryAssistantForTurn =
                message.role === "assistant" &&
                precedingUserIndex >= 0 &&
                messages.findIndex(
                  (item, itemIndex) => itemIndex > precedingUserIndex && item.role === "assistant",
                ) === index;

              const liveSteps =
                isPrimaryAssistantForTurn &&
                precedingUserId !== undefined &&
                liveUserMessageId === precedingUserId
                  ? timelineSteps.filter((step) => step.afterMessageId === precedingUserId)
                  : [];
              const historicalTools =
                isPrimaryAssistantForTurn &&
                precedingUserId !== undefined &&
                liveUserMessageId !== precedingUserId
                  ? historyToolCalls.filter(
                      (toolCall) => toolCall.afterMessageId === precedingUserId,
                    )
                  : [];

              const processChildren =
                message.role === "assistant"
                  ? [
                      ...liveSteps.map((step) => {
                        if (step.kind === "thinking") {
                          return <ThinkingStepCard key={step.id} step={step} />;
                        }

                        return (
                          <ToolCallCard
                            key={step.id}
                            toolCall={step}
                            onToggle={() => toggleTimelineStep(step.id)}
                          />
                        );
                      }),
                      ...historicalTools.map((toolCall) => (
                        <ToolCallCard
                          key={toolCall.id}
                          toolCall={toolCall}
                          onToggle={() => toggleHistoryToolCall(toolCall.id)}
                        />
                      )),
                    ]
                  : [];

              // Edge case: tools/thinking arrived before the assistant placeholder exists.
              // Keep a temporary stack under the live user message so the turn is not blank.
              const orphanLiveSteps =
                message.role === "user" &&
                liveUserMessageId === message.id &&
                currentAssistantMessageIndex < 0
                  ? timelineSteps.filter((step) => step.afterMessageId === message.id)
                  : [];

              return (
                <Fragment key={message.id}>
                  <article
                    className={`agentos-message ${
                      message.role === "user"
                        ? "agentos-message-user"
                        : `agentos-message-assistant ${
                            isStreaming && index === currentAssistantMessageIndex
                              ? "agentos-message-streaming"
                              : ""
                          }`
                    }`}
                  >
                    <div className="agentos-message-meta-row">
                      <p className="agentos-message-author">
                        {message.role === "user" ? "你" : "助手"}
                        {message.createdAt ? (
                          <span className="agentos-message-meta">
                            {" "}
                            · {formatMessageTimestamp(message.createdAt)}
                          </span>
                        ) : null}
                        {message.role === "assistant" && message.durationLabel ? (
                          <span className="agentos-message-meta"> · {message.durationLabel}</span>
                        ) : null}
                      </p>
                      {message.role === "assistant" && message.content ? (
                        <button
                          type="button"
                          onClick={() => void copyAssistantMessage(message)}
                          className="agentos-copy-button"
                        >
                          {copiedMessageId === message.id ? "已复制" : "复制"}
                        </button>
                      ) : null}
                    </div>

                    {processChildren.length > 0 ? (
                      <div className="agentos-message-process">{processChildren}</div>
                    ) : null}

                    {message.content ? (
                      message.role === "assistant" ? (
                        <AssistantMarkdown content={message.content} />
                      ) : (
                        (() => {
                          const { displayText, artifactIds } = parseUserMessageAttachments(
                            message.content,
                          );
                          return (
                            <div className="space-y-2">
                              {artifactIds.length > 0 ? (
                                <div
                                  className="agentos-message-attachments flex flex-wrap gap-2"
                                  aria-label="附件"
                                >
                                  {artifactIds.map((artifactId) => (
                                    <a
                                      key={artifactId}
                                      href={uploadContentUrl(artifactId)}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="agentos-upload-thumb block overflow-hidden"
                                      title="查看附件"
                                    >
                                      {/* Heuristic: try image; PDF still loads as object/download via link */}
                                      <img
                                        src={uploadContentUrl(artifactId)}
                                        alt=""
                                        className="agentos-upload-thumb-image h-20 w-20 object-cover"
                                        onError={(event) => {
                                          const target = event.currentTarget;
                                          target.style.display = "none";
                                          const fallback = target.nextElementSibling;
                                          if (fallback instanceof HTMLElement) {
                                            fallback.hidden = false;
                                          }
                                        }}
                                      />
                                      <span
                                        hidden
                                        className="agentos-upload-chip inline-flex h-20 w-20 items-center justify-center px-2 text-center text-[11px] leading-tight"
                                      >
                                        文件
                                      </span>
                                    </a>
                                  ))}
                                </div>
                              ) : null}
                              {displayText ? (
                                <p className="break-words whitespace-pre-wrap">{displayText}</p>
                              ) : null}
                            </div>
                          );
                        })()
                      )
                    ) : message.role === "assistant" && isStreaming ? (
                      <p aria-live="polite" className="agentos-chat-subheading">
                        {processChildren.length > 0 ? "继续生成中…" : "正在生成最终回答..."}
                      </p>
                    ) : null}
                  </article>

                  {orphanLiveSteps.length > 0 ? (
                    <article className="agentos-message agentos-message-assistant agentos-message-streaming">
                      <div className="agentos-message-meta-row">
                        <p className="agentos-message-author">助手</p>
                      </div>
                      <div className="agentos-message-process">
                        {orphanLiveSteps.map((step) => {
                          if (step.kind === "thinking") {
                            return <ThinkingStepCard key={step.id} step={step} />;
                          }
                          return (
                            <ToolCallCard
                              key={step.id}
                              toolCall={step}
                              onToggle={() => toggleTimelineStep(step.id)}
                            />
                          );
                        })}
                      </div>
                      <p aria-live="polite" className="agentos-chat-subheading">
                        继续生成中…
                      </p>
                    </article>
                  ) : null}
                </Fragment>
              );
            })
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {error ? (
        <p role="alert" className="agentos-chat-error border-t px-5 py-3 text-sm">
          {error}
        </p>
      ) : null}

      {approvalRunId !== null && pendingInterrupts.length > 0 ? (
        <div className="agentos-approval-wrap">
          <ApprovalPanel
            runId={approvalRunId}
            interrupts={pendingInterrupts}
            onResolved={() => void handleApprovalResolved()}
            onError={(message) => setError(message)}
          />
        </div>
      ) : null}

      {showScrollToLatest ? (
        <div className="agentos-scroll-latest-bar">
          <button
            type="button"
            onClick={scrollToLatest}
            className="agentos-scroll-latest px-3 py-2 text-xs font-medium"
          >
            回到最新消息
          </button>
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="agentos-composer">
        {uploadedArtifacts.length > 0 ? (
          <div className="agentos-upload-pending-list" aria-label="待发送附件">
            {uploadedArtifacts.map((artifact) => (
              <div
                key={artifact.artifactId}
                className="agentos-upload-pending relative inline-flex flex-col items-stretch"
              >
                {isImageMimeType(artifact.mimeType) ? (
                  <img
                    src={uploadContentUrl(artifact.artifactId)}
                    alt={artifact.title}
                    className="agentos-upload-thumb-image h-16 w-16 rounded-md object-cover"
                    title={artifact.title}
                  />
                ) : (
                  <span
                    className="agentos-upload-chip inline-flex h-16 max-w-36 flex-col justify-center gap-0.5 px-2.5 py-1 text-xs"
                    title={artifact.title}
                  >
                    <span className="font-medium">PDF</span>
                    <span className="truncate">{artifact.title}</span>
                  </span>
                )}
                <button
                  type="button"
                  className="agentos-upload-remove absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full text-[10px] leading-none"
                  aria-label={`移除 ${artifact.title}`}
                  onClick={() =>
                    setUploadedArtifacts((current) =>
                      current.filter((item) => item.artifactId !== artifact.artifactId),
                    )
                  }
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : null}

        {uploadNotice !== null ? (
          <p role="status" aria-live="polite" className="agentos-upload-notice">
            {uploadNotice}
          </p>
        ) : null}

        <textarea
          ref={textareaRef}
          aria-label="输入消息"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={
            isStreaming ||
            isUploading ||
            isLoadingHistory ||
            historyLoadFailed ||
            pendingInterrupts.length > 0
          }
          maxLength={4_000}
          placeholder={
            pendingInterrupts.length > 0
              ? "请先确认或取消上方的操作"
              : "输入任务、问题或需要助手处理的内容"
          }
          rows={1}
          className="agentos-composer-input block w-full resize-none outline-none disabled:cursor-not-allowed"
        />

        <div className="agentos-composer-toolbar">
          <div className="agentos-composer-tools">
            <input
              ref={fileInputRef}
              type="file"
              accept={UPLOAD_ACCEPT}
              multiple
              className="sr-only"
              onChange={(event) => void handleFileSelection(event)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={
                isStreaming ||
                isUploading ||
                isLoadingHistory ||
                historyLoadFailed ||
                pendingInterrupts.length > 0 ||
                uploadedArtifacts.length >= MAX_UPLOAD_FILES
              }
              className="agentos-upload-button disabled:cursor-not-allowed disabled:opacity-40"
              title="上传 PDF 或图片（新建会话会自动创建）"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                className="h-4 w-4"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="m18.4 12.6-6.9 6.9a5 5 0 0 1-7.1-7.1l8.2-8.2a3.5 3.5 0 1 1 5 5l-8.2 8.2a2 2 0 1 1-2.8-2.8l7.5-7.5"
                />
              </svg>
              {isUploading ? "上传中…" : "上传"}
            </button>
            <span className="agentos-composer-meta">
              {uploadedArtifacts.length}/{MAX_UPLOAD_FILES}
              <span className="hidden sm:inline"> · {draft.length}/4000 · Shift + Enter 换行</span>
            </span>
          </div>

          <button
            type="submit"
            disabled={
              isLoadingHistory ||
              isUploading ||
              historyLoadFailed ||
              pendingInterrupts.length > 0 ||
              (!isStreaming && !draft.trim() && uploadedArtifacts.length === 0)
            }
            className={`agentos-send-button disabled:cursor-not-allowed disabled:opacity-45 ${
              isStreaming ? "agentos-stop-button" : ""
            }`}
            aria-label={isStreaming ? "停止执行" : "发送消息"}
          >
            <span aria-hidden="true">{isStreaming ? "■" : "↑"}</span>
            <span>{isStreaming ? "停止执行" : "发送"}</span>
          </button>
        </div>
      </form>
    </section>
  );
}
