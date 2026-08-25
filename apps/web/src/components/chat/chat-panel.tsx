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
import { SessionStatsBar, type LiveRunStats } from "@/components/chat/session-stats-bar";
import { ThinkingStepCard, type ThinkingStepState } from "@/components/chat/thinking-step-card";
import {
  SandboxFilePreviewPane,
  sandboxFilesFromValue,
  summarizeToolResultContent,
  ToolCallCard,
  UploadPreviewPane,
  type SandboxFile,
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

const MAX_VISIBLE_REASONING_CHARS = 12_000;

function visibleReasoningContent(buffer: string): string {
  if (buffer.length <= MAX_VISIBLE_REASONING_CHARS) {
    return buffer;
  }

  return `…${buffer.slice(-MAX_VISIBLE_REASONING_CHARS)}`;
}

type ThreadLatestRun = {
  id: string;
  status: string;
};

// AG-UI wire event as parsed from the HITL resume SSE stream (camelCase,
// `exclude_none` serialization — every field except `type` may be absent).
type ResumeStreamEvent = {
  type?: string;
  messageId?: string;
  toolCallId?: string;
  toolCallName?: string;
  delta?: string;
  content?: string;
  subtype?: string;
  message?: string;
};

type ThreadHistory = {
  thread_id: string;
  agent_id: string;
  title: string | null;
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
  agentName: string;
  agentLoadError: string | null;
  /** False when the selected agent's model cannot take image input; upload stays disabled. */
  supportsVision?: boolean;
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
  "阅读并理解代码",
  "构建新功能、应用或工具",
  "审查代码并提出修改建议",
  "修复问题与故障",
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
      (item.duration_ms !== undefined &&
        item.duration_ms !== null &&
        (typeof item.duration_ms !== "number" || !Number.isFinite(item.duration_ms))) ||
      (item.provider !== undefined &&
        item.provider !== null &&
        typeof item.provider !== "string") ||
      (item.result !== undefined && item.result !== null && typeof item.result !== "string")
    ) {
      return null;
    }

    // Rich cards (knowledge hits, search links, artifact text) re-render from the
    // bounded raw result persisted with the tool_result event when available.
    const resultData =
      typeof item.result === "string"
        ? summarizeToolResultContent(item.result).resultData
        : undefined;

    toolCalls.push({
      id: item.id,
      toolName: item.tool_name,
      argsText: JSON.stringify(item.args),
      status: item.status,
      resultSummary: item.summary,
      resultData,
      provider: typeof item.provider === "string" ? item.provider : undefined,
      durationMs: typeof item.duration_ms === "number" ? item.duration_ms : undefined,
      expanded: false,
      afterMessageId: item.after_message_id,
      files: sandboxFilesFromValue(item.files),
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

  if (value.title !== undefined && value.title !== null && typeof value.title !== "string") {
    return null;
  }

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
    title: typeof value.title === "string" ? value.title : null,
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

    // Reuse the prior object when nothing changed so React.memo'd children
    // (e.g. AssistantMarkdown) can bail out instead of re-rendering every
    // historical message on each streamed token of the live one.
    if (prior !== undefined && prior.role === message.role && prior.content === message.content) {
      displayMessages.push(prior);
      continue;
    }

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

type TurnMeta = { precedingUserId: string | undefined; isPrimaryAssistantForTurn: boolean };

// Process steps are keyed to the user message that started the run; render them
// inside the first following assistant bubble only (later assistant messages in
// the same turn, if any, get no timeline attached).
function computeTurnMeta(messages: readonly ChatMessage[]): TurnMeta[] {
  const result: TurnMeta[] = [];
  let currentUserId: string | undefined;
  let assignedForCurrentUser = false;

  for (const message of messages) {
    if (message.role === "user") {
      currentUserId = message.id;
      assignedForCurrentUser = false;
      result.push({ precedingUserId: undefined, isPrimaryAssistantForTurn: false });
      continue;
    }
    if (currentUserId !== undefined && !assignedForCurrentUser) {
      assignedForCurrentUser = true;
      result.push({ precedingUserId: currentUserId, isPrimaryAssistantForTurn: true });
      continue;
    }
    result.push({ precedingUserId: currentUserId, isPrimaryAssistantForTurn: false });
  }

  return result;
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
      resultData: step.resultData,
      files: step.files,
      provider: step.provider,
      startedAt: step.startedAt,
      durationMs: step.durationMs,
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
  agentName,
  agentLoadError,
  supportsVision = true,
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
  const [threadTitle, setThreadTitle] = useState<string | null>(null);
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
  const [selectedSandboxFile, setSelectedSandboxFile] = useState<SandboxFile | null>(null);
  const [selectedUploadId, setSelectedUploadId] = useState<string | null>(null);

  // One side preview slot: opening one kind of file closes the other.
  const handleSandboxFileSelect = (file: SandboxFile) => {
    setSelectedUploadId(null);
    setSelectedSandboxFile(file);
  };
  const handleUploadSelect = (artifactId: string) => {
    setSelectedSandboxFile(null);
    setSelectedUploadId(artifactId);
  };
  const [isUploading, setIsUploading] = useState(false);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  // Live status-bar facts for the in-flight run (send/approval-click → first content).
  const [liveRunStats, setLiveRunStats] = useState<LiveRunStats | null>(null);

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
  // SSE delivers one onMessagesChanged per streamed token; coalescing to one
  // setState per animation frame keeps re-render (and the scroll-follow effect
  // it triggers) at a smooth ~60fps instead of jittering on every token.
  const pendingStreamMessagesRef = useRef<readonly Message[] | null>(null);
  const streamFlushHandleRef = useRef<number | null>(null);
  // Same rAF coalescing for text streamed by a resumed (post-approval) run,
  // which bypasses HttpAgent and therefore onMessagesChanged.
  const resumeDraftContentsRef = useRef<Map<string, string>>(new Map());
  const resumeDraftFlushHandleRef = useRef<number | null>(null);

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

  function scheduleResumeDraftFlush() {
    if (resumeDraftFlushHandleRef.current !== null) {
      return;
    }
    resumeDraftFlushHandleRef.current = window.requestAnimationFrame(() => {
      resumeDraftFlushHandleRef.current = null;
      const snapshot = new Map(resumeDraftContentsRef.current);
      if (snapshot.size === 0) {
        return;
      }
      if ([...snapshot.values()].some((content) => content.length > 0)) {
        markFirstLiveToken();
      }
      // The resumed run's text rides into the same message list as a draft
      // assistant message; the history reload on completion replaces it with
      // the persisted transcript.
      setMessages((previous) => {
        const next = [...previous];
        let changed = false;
        for (const [messageId, content] of snapshot) {
          const index = next.findIndex((message) => message.id === messageId);
          const existing = index === -1 ? undefined : next[index];
          if (existing === undefined) {
            next.push({ id: messageId, role: "assistant", content });
            changed = true;
          } else if (existing.content !== content) {
            next[index] = { ...existing, content };
            changed = true;
          }
        }
        return changed ? next : previous;
      });
    });
  }

  // Stream a resumed (post-approval) run's live events into the same timeline
  // state the initial run uses. Returns false when no stream is available (or
  // it broke before a terminal event) so the caller can fall back to polling.
  async function streamResumedRun(runId: string): Promise<boolean> {
    let response: Response;
    try {
      response = await fetch(`/api/runs/${runId}/stream`, { cache: "no-store" });
    } catch {
      return false;
    }
    if (response.status !== 200 || response.body === null) {
      return false;
    }

    const anchorId =
      liveUserMessageId ??
      [...messages].reverse().find((message) => message.role === "user")?.id ??
      "";
    if (anchorId !== "" && liveUserMessageId === null) {
      // Page reloaded while waiting for approval: re-anchor the turn so live
      // steps render in this turn's bubble instead of being dropped.
      setLiveUserMessageId(anchorId);
    }

    const argsBuffers = new Map<string, string>();
    const toolNames = new Map<string, string>();
    const reasoningBuffers = new Map<string, string>();
    let currentReasoningId: string | null = null;
    let sawTerminal = false;

    const applyEvent = (event: ResumeStreamEvent) => {
      switch (event.type) {
        case "TOOL_CALL_START": {
          const toolCallId = event.toolCallId ?? "";
          const toolCallName = event.toolCallName ?? "tool";
          toolNames.set(toolCallId, toolCallName);
          handleToolCallStart(anchorId, toolCallId, toolCallName);
          break;
        }
        case "TOOL_CALL_ARGS": {
          const toolCallId = event.toolCallId ?? "";
          const buffer = (argsBuffers.get(toolCallId) ?? "") + (event.delta ?? "");
          argsBuffers.set(toolCallId, buffer);
          handleToolCallArgs(anchorId, toolCallId, toolNames.get(toolCallId) ?? "tool", buffer);
          break;
        }
        case "TOOL_CALL_END": {
          const toolCallId = event.toolCallId ?? "";
          handleToolCallEnd(anchorId, toolCallId, toolNames.get(toolCallId) ?? "tool");
          break;
        }
        case "TOOL_CALL_RESULT": {
          handleToolCallResult(anchorId, event.toolCallId ?? "", event.content ?? "");
          break;
        }
        case "REASONING_START": {
          currentReasoningId = event.messageId ?? null;
          if (currentReasoningId !== null) {
            handleReasoningStart(anchorId, currentReasoningId);
          }
          break;
        }
        case "REASONING_MESSAGE_START": {
          handleReasoningMessageStart(anchorId);
          break;
        }
        case "REASONING_MESSAGE_CONTENT": {
          const messageId = event.messageId ?? currentReasoningId;
          if (messageId !== null) {
            const buffer = (reasoningBuffers.get(messageId) ?? "") + (event.delta ?? "");
            reasoningBuffers.set(messageId, buffer);
            handleReasoningMessageContent(anchorId, buffer);
          }
          break;
        }
        case "REASONING_MESSAGE_END": {
          handleReasoningMessageEnd(anchorId);
          break;
        }
        case "REASONING_ENCRYPTED_VALUE": {
          handleReasoningEncryptedValue(anchorId, event.subtype);
          break;
        }
        case "REASONING_END": {
          handleReasoningEnd(anchorId, event.messageId ?? currentReasoningId ?? "");
          break;
        }
        case "TEXT_MESSAGE_START": {
          resumeDraftContentsRef.current.set(event.messageId ?? "", "");
          scheduleResumeDraftFlush();
          break;
        }
        case "TEXT_MESSAGE_CONTENT": {
          const messageId = event.messageId ?? "";
          resumeDraftContentsRef.current.set(
            messageId,
            (resumeDraftContentsRef.current.get(messageId) ?? "") + (event.delta ?? ""),
          );
          scheduleResumeDraftFlush();
          break;
        }
        case "RUN_ERROR": {
          sawTerminal = true;
          setError(event.message ?? "助手处理失败，请稍后重试。");
          break;
        }
        case "RUN_FINISHED": {
          sawTerminal = true;
          break;
        }
        default:
          break;
      }
    };

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let pending = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        pending += decoder.decode(value, { stream: true });
        let separator = pending.indexOf("\n\n");
        while (separator !== -1) {
          const frame = pending.slice(0, separator);
          pending = pending.slice(separator + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data:")) {
              continue;
            }
            const raw = line.slice(5).trim();
            if (raw === "") {
              continue;
            }
            applyEvent(JSON.parse(raw) as ResumeStreamEvent);
          }
          separator = pending.indexOf("\n\n");
        }
      }
    } catch {
      return false;
    }
    return sawTerminal;
  }

  async function handleApprovalResolved() {
    const runId = approvalRunId;
    clearApprovalState();
    if (runId === null) {
      return;
    }

    setIsStreaming(true);
    setLiveRunStats({ startedAt: Date.now(), firstTokenMs: null });
    setError(null);
    resumeDraftContentsRef.current = new Map();
    activeRunIdRef.current = runId;

    // Preferred path: live stream of the resumed run (tool calls, reasoning,
    // and text appear as they happen instead of after a polling blackout).
    const streamed = await streamResumedRun(runId);
    if (streamed) {
      activeRunIdRef.current = null;
      setIsStreaming(false);
      setHistoryRefreshKey((current) => current + 1);
      onRunFinalized();
      // The resume may have paused again on a fresh approval request.
      await applyApprovalStateFromRun(runId);
      return;
    }
    activeRunIdRef.current = null;

    // Fallback: poll status until terminal, then reload history in one shot.
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
      if (streamFlushHandleRef.current !== null) {
        window.cancelAnimationFrame(streamFlushHandleRef.current);
      }
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
            setSelectedSandboxFile(null);
          }
          agentRef.current = createAgent(history.thread_id, history.messages, agentId);
          setMessages(history.messages);
          setThreadId(history.thread_id);
          setThreadTitle(history.title);
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

  // First visible assistant content of the in-flight run → the live "首 token" figure.
  function markFirstLiveToken() {
    setLiveRunStats((current) =>
      current !== null && current.firstTokenMs === null
        ? { ...current, firstTokenMs: Date.now() - current.startedAt }
        : current,
    );
  }

  function scheduleMessagesFlush(nextMessages: readonly Message[]) {
    pendingStreamMessagesRef.current = nextMessages;
    if (streamFlushHandleRef.current !== null) {
      return;
    }
    streamFlushHandleRef.current = window.requestAnimationFrame(() => {
      streamFlushHandleRef.current = null;
      const latest = pendingStreamMessagesRef.current;
      pendingStreamMessagesRef.current = null;
      if (latest === null) {
        return;
      }
      if (
        latest.some(
          (message) =>
            message.role === "assistant" &&
            typeof message.content === "string" &&
            message.content.length > 0,
        )
      ) {
        markFirstLiveToken();
      }
      setMessages((previous) => toDisplayMessages(latest, previous));
    });
  }

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

  // Timeline event handlers shared by the live HttpAgent run and the HITL
  // resume stream: both map AG-UI events onto the same timeline state, keyed
  // to the user message that started the turn (anchorId).
  function handleReasoningStart(anchorId: string, messageId: string) {
    setTimelineSteps((current) =>
      upsertTimelineStep(current, {
        kind: "thinking",
        id: messageId,
        content: "",
        phase: "正在理解问题与相关上下文",
        contentMode: "none",
        status: "running",
        startedAt: Date.now(),
        expanded: false,
        afterMessageId: anchorId,
      }),
    );
  }

  function handleReasoningMessageStart(anchorId: string) {
    setTimelineSteps((current) =>
      updateLatestThinkingStep(current, anchorId, {
        phase: "正在拆解任务目标",
      }),
    );
  }

  function handleReasoningMessageContent(anchorId: string, reasoningMessageBuffer: string) {
    if (!reasoningMessageBuffer.trim()) {
      return;
    }

    setTimelineSteps((current) =>
      updateLatestThinkingStep(current, anchorId, {
        content: visibleReasoningContent(reasoningMessageBuffer),
        phase: "正在分析",
        contentMode: "text",
      }),
    );
  }

  function handleReasoningMessageEnd(anchorId: string) {
    setTimelineSteps((current) =>
      updateLatestThinkingStep(current, anchorId, {
        phase: "分析完成，正在准备下一步",
      }),
    );
  }

  function handleReasoningEncryptedValue(anchorId: string, subtype: string | undefined) {
    if (subtype !== "message") {
      return;
    }

    setTimelineSteps((current) => {
      // Responses providers always attach an encrypted signature for turn
      // continuity — even when a readable summary already streamed. Only mark
      // the step "encrypted" when there is no readable content to show.
      for (let index = current.length - 1; index >= 0; index -= 1) {
        const step = current[index];
        if (step?.kind !== "thinking" || step.afterMessageId !== anchorId) {
          continue;
        }
        if (step.content.trim()) {
          return current;
        }
        const next = [...current];
        next[index] = {
          ...step,
          phase: "模型返回了加密 reasoning",
          contentMode: "encrypted",
        };
        return next;
      }
      return current;
    });
  }

  function handleReasoningEnd(anchorId: string, messageId: string) {
    setTimelineSteps((current) =>
      current.map((step) =>
        step.kind === "thinking" && step.id === messageId
          ? {
              ...step,
              status: "done",
              phase: "思考完成",
              durationMs: Math.max(0, Date.now() - step.startedAt),
              expanded: false,
            }
          : step,
      ),
    );
  }

  function handleToolCallStart(anchorId: string, toolCallId: string, toolCallName: string) {
    setTimelineSteps((current) => {
      const next = updateLatestThinkingStep(current, anchorId, {
        phase: "正在调用相关能力",
      });
      return upsertTimelineStep(next, {
        kind: "tool",
        id: toolCallId,
        toolName: toolCallName,
        argsText: "",
        status: "running",
        expanded: false,
        afterMessageId: anchorId,
      });
    });
  }

  function handleToolCallArgs(
    anchorId: string,
    toolCallId: string,
    toolCallName: string,
    toolCallBuffer: string,
  ) {
    setTimelineSteps((current) => {
      const existing = current.find(
        (step): step is ToolTimelineStep => step.kind === "tool" && step.id === toolCallId,
      );
      return upsertTimelineStep(current, {
        kind: "tool",
        id: toolCallId,
        toolName: toolCallName || existing?.toolName || "tool",
        argsText: toolCallBuffer,
        status: existing?.status ?? "running",
        resultSummary: existing?.resultSummary,
        provider: existing?.provider,
        startedAt: existing?.startedAt ?? Date.now(),
        durationMs: existing?.durationMs,
        expanded: existing?.expanded ?? false,
        afterMessageId: existing?.afterMessageId ?? anchorId,
      });
    });
  }

  function handleToolCallEnd(
    anchorId: string,
    toolCallId: string,
    toolCallName: string,
    toolCallArgs?: unknown,
  ) {
    setTimelineSteps((current) => {
      const existing = current.find(
        (step): step is ToolTimelineStep => step.kind === "tool" && step.id === toolCallId,
      );
      return upsertTimelineStep(current, {
        kind: "tool",
        id: toolCallId,
        toolName: toolCallName || existing?.toolName || "tool",
        argsText: existing?.argsText || JSON.stringify(toolCallArgs ?? {}),
        status: existing?.status ?? "running",
        resultSummary: existing?.resultSummary,
        provider: existing?.provider,
        startedAt: existing?.startedAt ?? Date.now(),
        durationMs: existing?.durationMs,
        expanded: existing?.expanded ?? false,
        afterMessageId: existing?.afterMessageId ?? anchorId,
      });
    });
  }

  function handleToolCallResult(anchorId: string, toolCallId: string, content: string) {
    const summarized = summarizeToolResultContent(content);
    setTimelineSteps((current) => {
      const existing = current.find(
        (step): step is ToolTimelineStep => step.kind === "tool" && step.id === toolCallId,
      );
      return upsertTimelineStep(current, {
        kind: "tool",
        id: toolCallId,
        toolName: existing?.toolName || "tool",
        argsText: existing?.argsText || "",
        status: summarized.status,
        resultSummary: summarized.summary,
        resultData: summarized.resultData ?? existing?.resultData,
        files: sandboxFilesFromValue(summarized.resultData?.files),
        provider: summarized.provider ?? existing?.provider,
        startedAt: existing?.startedAt ?? Date.now(),
        durationMs:
          existing?.durationMs ??
          (existing?.startedAt === undefined ? undefined : Date.now() - existing.startedAt),
        expanded: false,
        afterMessageId: existing?.afterMessageId ?? anchorId,
      });
    });
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
    setLiveRunStats({ startedAt: Date.now(), firstTokenMs: null });
    autoScrollRef.current = true;
    setShowScrollToLatest(false);
    cancellationRequestedRef.current = false;

    let activeRunId: string | null = null;
    let recoverRunId: string | null = null;
    deferredStreamErrorRef.current = null;

    try {
      await agent.runAgent(undefined, {
        onMessagesChanged: ({ messages: nextMessages }) => {
          scheduleMessagesFlush(nextMessages);
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
          handleReasoningStart(userMessageId, event.messageId);
        },
        onReasoningMessageStartEvent: () => {
          handleReasoningMessageStart(userMessageId);
        },
        onReasoningMessageContentEvent: ({ reasoningMessageBuffer }) => {
          handleReasoningMessageContent(userMessageId, reasoningMessageBuffer);
        },
        onReasoningMessageEndEvent: () => {
          handleReasoningMessageEnd(userMessageId);
        },
        onReasoningEncryptedValueEvent: ({ event }) => {
          handleReasoningEncryptedValue(userMessageId, event.subtype);
        },
        onReasoningEndEvent: ({ event }) => {
          handleReasoningEnd(userMessageId, event.messageId);
        },
        onToolCallStartEvent: ({ event }) => {
          handleToolCallStart(userMessageId, event.toolCallId, event.toolCallName);
        },
        onToolCallArgsEvent: ({ event, toolCallBuffer, toolCallName }) => {
          handleToolCallArgs(userMessageId, event.toolCallId, toolCallName, toolCallBuffer);
        },
        onToolCallEndEvent: ({ event, toolCallName, toolCallArgs }) => {
          handleToolCallEnd(userMessageId, event.toolCallId, toolCallName, toolCallArgs);
        },
        onToolCallResultEvent: ({ event }) => {
          handleToolCallResult(userMessageId, event.toolCallId, event.content);
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
      setThreadTitle(null);
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
      setSelectedSandboxFile(null);
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

  // One O(N) pass instead of an O(N) backward scan + O(N) findIndex per assistant
  // row (was O(N^2) over the whole list on every render, including every streamed
  // token of the live reply).
  const turnMetaByIndex = computeTurnMeta(messages);

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
        : "";

  const liveToolCallCount = timelineSteps.reduce(
    (count, step) => (step.kind === "tool" ? count + 1 : count),
    0,
  );
  const liveAssistantChars =
    isStreaming && currentAssistantMessageIndex >= 0
      ? messages[currentAssistantMessageIndex].content.length
      : 0;
  const fallbackTitle = messages
    .find((message) => message.role === "user")
    ?.content.replace(/\s+/g, " ")
    .trim()
    .slice(0, 48);
  const displayThreadTitle =
    threadTitle?.trim() || fallbackTitle || (threadId === null ? "新建会话" : "未命名会话");

  return (
    <section
      className={`agentos-chat-panel flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden ${
        isStreaming ? "agentos-is-streaming" : ""
      }`}
    >
      <header className="agentos-chat-header">
        <div className="agentos-thread-heading">
          <p className="agentos-thread-kicker">{agentName}</p>
          <div className="agentos-thread-title-row">
            <h1 className="agentos-chat-heading">{displayThreadTitle}</h1>
            {statusLabel ? (
              <p aria-live="polite" className="agentos-chat-status">
                <span aria-hidden="true" />
                {statusLabel}
              </p>
            ) : null}
          </div>
          <p className="agentos-chat-subheading">
            {threadId === null ? "准备开始新的任务" : "AgentOS 会话"}
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

      <div className="agentos-chat-body">
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
              <div className="agentos-empty-state agentos-codex-empty-state">
                <span className="agentos-codex-glyph" aria-hidden="true">
                  <svg viewBox="0 0 48 48" fill="none">
                    <path
                      d="M24 5.5c3.4 0 6.3 1.8 7.8 4.5 3.1-.7 6.4.2 8.8 2.6 2.4 2.4 3.3 5.7 2.6 8.8 2.7 1.5 4.5 4.4 4.5 7.8s-1.8 6.3-4.5 7.8c.7 3.1-.2 6.4-2.6 8.8-2.4 2.4-5.7 3.3-8.8 2.6-1.5 2.7-4.4 4.5-7.8 4.5s-6.3-1.8-7.8-4.5c-3.1.7-6.4-.2-8.8-2.6-2.4-2.4-3.3-5.7-2.6-8.8C2.8 35.5 1 32.6 1 29.2s1.8-6.3 4.5-7.8c-.7-3.1.2-6.4 2.6-8.8 2.4-2.4 5.7-3.3 8.8-2.6C17.7 7.3 20.6 5.5 24 5.5Z"
                      stroke="currentColor"
                      strokeWidth="2.8"
                    />
                    <path
                      d="M18.5 23.5c-2.1 1-2.1 5.7 0 6.8M29.5 23.5c2.1 1 2.1 5.7 0 6.8M20.5 34h7"
                      stroke="currentColor"
                      strokeWidth="2.8"
                      strokeLinecap="round"
                    />
                  </svg>
                </span>
                <p className="agentos-chat-heading">What should we work on?</p>
                <div className="agentos-starter-prompts">
                  {STARTER_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => applyStarterPrompt(prompt)}
                      className="agentos-starter-prompt agentos-codex-starter-prompt max-w-full text-left text-sm"
                    >
                      <span className="agentos-codex-starter-icon" aria-hidden="true">
                        {prompt === STARTER_PROMPTS[0]
                          ? "⌘"
                          : prompt === STARTER_PROMPTS[1]
                            ? "⌁"
                            : prompt === STARTER_PROMPTS[2]
                              ? "◌"
                              : "◉"}
                      </span>
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message, index) => {
                const { precedingUserId, isPrimaryAssistantForTurn } = turnMetaByIndex[index] ?? {
                  precedingUserId: undefined,
                  isPrimaryAssistantForTurn: false,
                };

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
                              onFileSelect={handleSandboxFileSelect}
                              selectedFilePath={selectedSandboxFile?.path ?? null}
                            />
                          );
                        }),
                        ...historicalTools.map((toolCall) => (
                          <ToolCallCard
                            key={toolCall.id}
                            toolCall={toolCall}
                            onToggle={() => toggleHistoryToolCall(toolCall.id)}
                            onFileSelect={(file) => setSelectedSandboxFile(file)}
                            selectedFilePath={selectedSandboxFile?.path ?? null}
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
                    <div
                      className={`agentos-message-wrap ${
                        message.role === "user"
                          ? "agentos-message-wrap-user"
                          : "agentos-message-wrap-assistant"
                      }`}
                    >
                      {message.role === "assistant" && processChildren.length > 0 ? (
                        <div className="agentos-message-process agentos-codex-turn-steps">
                          {processChildren}
                        </div>
                      ) : null}
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
                                        <button
                                          key={artifactId}
                                          type="button"
                                          onClick={() => handleUploadSelect(artifactId)}
                                          className="agentos-upload-thumb block overflow-hidden"
                                          title="预览附件"
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
                                        </button>
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

                      {message.role === "assistant" && message.content ? (
                        <div className="agentos-message-toolbar">
                          <button
                            type="button"
                            onClick={() => void copyAssistantMessage(message)}
                            className="agentos-copy-button"
                          >
                            {copiedMessageId === message.id ? "已复制" : "复制"}
                          </button>
                        </div>
                      ) : null}

                      {message.createdAt || message.durationLabel ? (
                        <div
                          className={`agentos-message-time ${
                            message.role === "user"
                              ? "agentos-message-time-user"
                              : "agentos-message-time-assistant"
                          }`}
                        >
                          {message.createdAt ? (
                            <time dateTime={message.createdAt}>
                              {formatMessageTimestamp(message.createdAt)}
                            </time>
                          ) : null}
                          {message.durationLabel ? (
                            <span>
                              {message.createdAt ? " · " : ""}
                              {message.durationLabel}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </div>

                    {orphanLiveSteps.length > 0 ? (
                      <div className="agentos-message-wrap agentos-message-wrap-assistant agentos-tool-only-turn">
                        <div className="agentos-message-process agentos-codex-turn-steps">
                          {orphanLiveSteps.map((step) => {
                            if (step.kind === "thinking") {
                              return <ThinkingStepCard key={step.id} step={step} />;
                            }
                            return (
                              <ToolCallCard
                                key={step.id}
                                toolCall={step}
                                onToggle={() => toggleTimelineStep(step.id)}
                                onFileSelect={handleSandboxFileSelect}
                                selectedFilePath={selectedSandboxFile?.path ?? null}
                              />
                            );
                          })}
                        </div>
                        <p aria-live="polite" className="agentos-chat-subheading">
                          继续生成中…
                        </p>
                      </div>
                    ) : null}
                  </Fragment>
                );
              })
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {selectedSandboxFile !== null ? (
          <SandboxFilePreviewPane
            file={selectedSandboxFile}
            onClose={() => setSelectedSandboxFile(null)}
          />
        ) : null}
        {selectedUploadId !== null ? (
          <UploadPreviewPane
            key={selectedUploadId}
            artifactId={selectedUploadId}
            onClose={() => setSelectedUploadId(null)}
          />
        ) : null}
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

        <div className="agentos-codex-agent-picker">
          <span aria-hidden="true">▱</span>
          <button type="button">{agentName}</button>
        </div>

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
              : "输入任务，@ 添加文件，/ 使用命令"
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
              className="agentos-upload-button agentos-codex-upload-button disabled:cursor-not-allowed disabled:opacity-40"
              title={
                supportsVision
                  ? "上传 PDF 或图片（新建会话会自动创建）"
                  : "当前模型以文本方式读取附件（不看图），结论以 OCR 文本为准"
              }
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
              <span className="agentos-codex-upload-label">{isUploading ? "上传中…" : ""}</span>
            </button>
            <button type="button" className="agentos-codex-custom-button">
              <span aria-hidden="true">⚙</span>
              自定义
            </button>
            <span className="agentos-composer-meta">
              {uploadedArtifacts.length > 0
                ? `${uploadedArtifacts.length}/${MAX_UPLOAD_FILES}`
                : null}
            </span>
          </div>

          <div className="agentos-codex-composer-actions">
            <span className="agentos-codex-model">模型⌄</span>
            <button type="button" className="agentos-codex-voice-button" aria-label="语音输入">
              ◉
            </button>
            <button
              type="submit"
              disabled={
                isLoadingHistory ||
                isUploading ||
                historyLoadFailed ||
                pendingInterrupts.length > 0 ||
                (!isStreaming && !draft.trim() && uploadedArtifacts.length === 0)
              }
              className={`agentos-send-button agentos-codex-send-button disabled:cursor-not-allowed disabled:opacity-45 ${
                isStreaming ? "agentos-stop-button" : ""
              }`}
              aria-label={isStreaming ? "停止执行" : "发送消息"}
            >
              <span aria-hidden="true">{isStreaming ? "■" : "↑"}</span>
              <span>{isStreaming ? "停止执行" : "发送"}</span>
            </button>
          </div>
        </div>

        <div className="agentos-codex-runtime-row" aria-label="运行环境">
          <span className="is-active">本地</span>
          <span>工作树</span>
          <span>云端</span>
          <span className="agentos-codex-branch">⌘ main 分支</span>
        </div>
      </form>

      <SessionStatsBar
        threadId={threadId}
        isStreaming={isStreaming}
        refreshKey={historyRefreshKey}
        liveStats={liveRunStats}
        liveToolCalls={liveToolCallCount}
        liveAssistantChars={liveAssistantChars}
      />
    </section>
  );
}
