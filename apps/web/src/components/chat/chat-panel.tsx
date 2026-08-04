"use client";

import { HttpAgent, type Message } from "@ag-ui/client";
import {
  FormEvent,
  Fragment,
  KeyboardEvent,
  TouchEvent,
  WheelEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { ApprovalPanel, type PendingInterrupt } from "@/components/chat/approval-panel";
import { AssistantMarkdown } from "@/components/chat/assistant-markdown";
import { ProcessGroup } from "@/components/chat/process-group";
import { ThinkingStepCard, type ThinkingStepState } from "@/components/chat/thinking-step-card";
import {
  summarizeToolResultContent,
  ToolCallCard,
  type ToolCallState,
} from "@/components/chat/tool-call-card";
import { formatMessageTimestamp, formatRunDurationLabel } from "@/lib/format-time";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt?: string;
  durationLabel?: string;
};

type ToolTimelineStep = ToolCallState & { kind: "tool" };

type TimelineStep = ThinkingStepState | ToolTimelineStep;

type ThreadHistory = {
  thread_id: string;
  messages: ChatMessage[];
  toolCalls: ToolCallState[];
};

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
  /** When false, this panel stays mounted for background runs but must not own the URL/inspector. */
  isActive?: boolean;
  onNewConversation: () => void;
  onRunStarted: (runId: string) => void;
  onStreamingChanged: (isStreaming: boolean) => void;
  onAwaitingApprovalChanged?: (isAwaiting: boolean) => void;
  onThreadChanged: (threadId: string | null) => void;
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

  return { thread_id: value.thread_id, messages, toolCalls };
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

function createAgent(threadId: string, messages: ChatMessage[]): HttpAgent {
  return new HttpAgent({
    url: "/api/ag-ui/runs",
    threadId,
    initialMessages: toAgentMessages(messages),
  });
}

function updateThreadInUrl(threadId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("thread", threadId);
  window.history.replaceState(window.history.state, "", url);
}

function agentErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.includes("409")) {
    return "当前会话仍在生成，请等待完成或停止当前请求。";
  }

  return "Agent 生成失败，请稍后重试。";
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
  isActive = true,
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

  const agentRef = useRef<HttpAgent | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const lastRunIdRef = useRef<string | null>(null);
  const lastRunThreadIdRef = useRef<string | null>(null);
  const cancellationRequestedRef = useRef(false);
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

  isActiveRef.current = isActive;

  if (agentRef.current === null) {
    agentRef.current = createAgent("new", []);
  }

  useEffect(() => {
    onStreamingChanged(isStreaming);
  }, [isStreaming, onStreamingChanged]);

  useEffect(() => {
    onAwaitingApprovalChanged?.(pendingInterrupts.length > 0);
  }, [pendingInterrupts, onAwaitingApprovalChanged]);

  function clearApprovalState() {
    setApprovalRunId(null);
    setPendingInterrupts([]);
  }

  async function applyApprovalStateFromRun(runId: string) {
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
  }

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
      if (state.status === "completed" || state.status === "failed" || state.status === "cancelled") {
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
    if (
      threadId === null ||
      lastRunIdRef.current === null ||
      lastRunThreadIdRef.current !== threadId
    ) {
      return;
    }

    let cancelled = false;

    async function refreshAfterBackgroundRun() {
      const runId = lastRunIdRef.current;
      if (runId === null || isStreaming) {
        return;
      }

      for (let attempt = 0; attempt < 30; attempt += 1) {
        if (cancelled) {
          return;
        }

        try {
          const response = await fetch(`/api/runs/${runId}`, { cache: "no-store" });
          if (!response.ok) {
            return;
          }

          const payload: unknown = await response.json();
          if (!isRecord(payload) || typeof payload.status !== "string") {
            return;
          }

          if (payload.status === "waiting_approval") {
            if (!cancelled) {
              await applyApprovalStateFromRun(runId);
              setHistoryRefreshKey((current) => current + 1);
            }
            return;
          }

          if (payload.status !== "running" && payload.status !== "queued") {
            if (!cancelled) {
              clearApprovalState();
              setHistoryRefreshKey((current) => current + 1);
            }
            return;
          }

          await new Promise<void>((resolve) => window.setTimeout(resolve, 1_000));
        } catch {
          return;
        }
      }
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refreshAfterBackgroundRun();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    if (document.visibilityState === "visible") {
      void refreshAfterBackgroundRun();
    }

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [isStreaming, threadId]);

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
          agentRef.current = createAgent(history.thread_id, history.messages);
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
          onThreadChanged(history.thread_id);
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
  }, [historyRefreshKey, onThreadChanged, selectedThreadId, threadId]);

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

  async function sendMessage() {
    const content = draft.trim();

    if (!content || isStreaming || isLoadingHistory || historyLoadFailed) {
      return;
    }

    const agent = agentRef.current;
    if (agent === null) {
      setError("聊天客户端尚未初始化。");
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
    setDraft("");
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
          setError(event.message || "Agent 生成失败，请稍后重试。");
        },
        onReasoningStartEvent: ({ event }) => {
          setTimelineSteps((current) =>
            upsertTimelineStep(current, {
              kind: "thinking",
              id: event.messageId,
              content: "",
              status: "running",
              // Compact 2-line preview; no tall expand panel.
              expanded: false,
              afterMessageId: userMessageId,
            }),
          );
        },
        onReasoningEndEvent: ({ event }) => {
          setTimelineSteps((current) =>
            current.map((step) =>
              step.kind === "thinking" && step.id === event.messageId
                ? { ...step, status: "done", expanded: false }
                : step,
            ),
          );
        },
        onToolCallStartEvent: ({ event }) => {
          // Keep tools as a single status line; details only after the user expands.
          setTimelineSteps((current) =>
            upsertTimelineStep(current, {
              kind: "tool",
              id: event.toolCallId,
              toolName: event.toolCallName,
              argsText: "",
              status: "running",
              expanded: false,
              afterMessageId: userMessageId,
            }),
          );
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
      if (!cancellationRequestedRef.current) {
        setError(agentErrorMessage(caughtError));
      } else if (activeRunId !== null) {
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
    } finally {
      const finishedRunId = activeRunId;
      const wasCancelled = cancellationRequestedRef.current;
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
    ? "读取会话中"
    : pendingInterrupts.length > 0
      ? "等待审批"
      : isStreaming
        ? "Agent 正在执行"
        : "Runtime ready";

  return (
    <section
      className={`agentos-chat-panel flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden border ${
        isStreaming ? "agentos-is-streaming" : ""
      }`}
    >
      <header className="agentos-chat-header flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3.5 sm:px-5 sm:py-4">
        <div>
          <p className="agentos-chat-heading text-sm font-semibold">
            {threadId === null ? "新建 Agent 会话" : "Agent conversation"}
          </p>
          <p className="agentos-chat-subheading mt-1 text-xs">
            {threadId === null ? "准备新的执行上下文" : "当前 Thread 已恢复"}
          </p>
        </div>

        <div className="flex items-center gap-3">
          <p aria-live="polite" className="agentos-chat-status text-xs">
            <span aria-hidden="true" />
            {statusLabel}
          </p>
          <button
            type="button"
            onClick={startNewConversation}
            disabled={isLoadingHistory}
            className="agentos-new-chat-button text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40"
          >
            新建对话
          </button>
        </div>
      </header>

      <div
        ref={messagesViewportRef}
        onScroll={handleMessageScroll}
        onWheel={handleViewportWheel}
        onTouchStart={handleViewportTouchStart}
        onTouchMove={handleViewportTouchMove}
        className="agentos-message-viewport min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 sm:p-5"
      >
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.length === 0 ? (
            <div className="agentos-empty-state flex min-h-72 flex-col justify-center py-8">
              <p className="agentos-chat-heading text-lg font-semibold">从一个任务开始</p>
              <p className="agentos-chat-subheading mt-2 max-w-lg text-sm leading-6">
                AgentOS 会在同一条运行轨迹中展示对话、思考过程与最终执行结果。
              </p>
              <div className="mt-6 flex flex-col items-start gap-2">
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
              const liveSteps =
                liveUserMessageId === message.id
                  ? timelineSteps.filter((step) => step.afterMessageId === message.id)
                  : [];
              const historicalTools =
                liveUserMessageId === message.id
                  ? []
                  : historyToolCalls.filter((toolCall) => toolCall.afterMessageId === message.id);

              // Duration for the process group comes from the following assistant reply.
              const followingAssistant =
                message.role === "user"
                  ? messages.slice(index + 1).find((item) => item.role === "assistant")
                  : undefined;
              const hasProcessShell = liveSteps.length > 0 || historicalTools.length > 0;
              // While tools/thinking are live, draft assistant text is analysis — keep it in
              // the process fold. After the turn ends, the same text becomes the conclusion outside.
              const foldAnalysisIntoProcess =
                message.role === "user" &&
                isStreaming &&
                liveUserMessageId === message.id &&
                hasProcessShell &&
                Boolean(followingAssistant?.content);
              const processStepsActive =
                message.role === "user" &&
                isStreaming &&
                liveUserMessageId === message.id &&
                (liveSteps.length > 0 ||
                  (followingAssistant !== undefined && !followingAssistant.content) ||
                  foldAnalysisIntoProcess);

              const processChildren =
                message.role === "user"
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
                      ...(foldAnalysisIntoProcess && followingAssistant
                        ? [
                            <div
                              key={`${message.id}-process-note`}
                              className="agentos-process-note"
                            >
                              <p className="agentos-process-note-label">分析</p>
                              <AssistantMarkdown content={followingAssistant.content} />
                            </div>,
                          ]
                        : []),
                    ]
                  : [];

              const suppressOutsideAssistant =
                message.role === "assistant" &&
                isStreaming &&
                index === currentAssistantMessageIndex &&
                timelineSteps.length > 0;

              return (
                <Fragment key={message.id}>
                  {suppressOutsideAssistant ? null : (
                    <article
                      className={`agentos-message ${
                        message.role === "user"
                          ? "agentos-message-user ml-auto max-w-[88%] sm:max-w-[72%]"
                          : `agentos-message-assistant max-w-full sm:max-w-[92%] ${
                              isStreaming && index === currentAssistantMessageIndex
                                ? "agentos-message-streaming"
                                : ""
                            }`
                      }`}
                    >
                      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
                        <p className="agentos-message-author">
                          {message.role === "user" ? "你" : "AgentOS"}
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

                      {message.content ? (
                        message.role === "assistant" ? (
                          <AssistantMarkdown content={message.content} />
                        ) : (
                          <p className="break-words whitespace-pre-wrap">{message.content}</p>
                        )
                      ) : message.role === "assistant" && isStreaming ? (
                        <p aria-live="polite" className="agentos-chat-subheading">
                          正在生成最终回答...
                        </p>
                      ) : null}
                    </article>
                  )}

                  {message.role === "user" && processChildren.length > 0 ? (
                    <ProcessGroup
                      isActive={processStepsActive}
                      durationLabel={followingAssistant?.durationLabel}
                    >
                      {processChildren}
                    </ProcessGroup>
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
        <div className="border-t px-3 py-3 sm:px-5">
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

      <form
        onSubmit={handleSubmit}
        className="agentos-composer border-t p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:p-4"
      >
        <textarea
          ref={textareaRef}
          aria-label="输入消息"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={
            isStreaming ||
            isLoadingHistory ||
            historyLoadFailed ||
            pendingInterrupts.length > 0
          }
          maxLength={4_000}
          placeholder={
            pendingInterrupts.length > 0
              ? "请先批准或拒绝上方的工具调用"
              : "输入任务、问题或需要 Agent 执行的操作"
          }
          rows={1}
          className="agentos-composer-input block max-h-50 w-full resize-none overflow-y-hidden px-3 py-2 text-sm leading-6 outline-none disabled:cursor-not-allowed"
        />

        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="agentos-chat-subheading text-xs">
            {draft.length}/4000 <span className="hidden sm:inline">Shift + Enter 换行</span>
          </span>

          <button
            type="submit"
            disabled={
              isLoadingHistory ||
              historyLoadFailed ||
              pendingInterrupts.length > 0 ||
              (!isStreaming && !draft.trim())
            }
            className={`agentos-send-button min-w-18 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-45 ${
              isStreaming ? "agentos-stop-button" : ""
            }`}
          >
            {isStreaming ? "停止执行" : "发送"}
          </button>
        </div>
      </form>
    </section>
  );
}
