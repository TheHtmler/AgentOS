/**
 * AG-UI → assistant-ui ExternalStoreRuntime adapter.
 *
 * Owns chat state (messages + isRunning) in React state, translates AG-UI wire events
 * (via `@ag-ui/client`'s `HttpAgent` subscriber callbacks, plus the HITL resume `/stream`
 * SSE consumed through `readAguiEventStream`) into assistant-ui `ThreadMessageLike`
 * messages, and exposes the adapter object consumed by `useExternalStoreRuntime`.
 */

import { HttpAgent, type Message } from "@ag-ui/client";
import type {
  AttachmentAdapter,
  AppendMessage,
  CompleteAttachment,
  PendingAttachment,
  ReasoningMessagePart,
  TextMessagePart,
  ThreadMessageLike,
  ToolCallMessagePart,
} from "@assistant-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { readAguiEventStream, type AguiStreamEvent } from "@/lib/agui-events";
import { isActiveRunStatus } from "@/lib/run-recovery";

const UPLOAD_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp";
const RECOVERY_DELAYS_MS = [1_000, 2_000, 5_000] as const;
const RECOVERY_MAX_ATTEMPTS = 60;

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function buildMessageWithArtifacts(text: string, artifactIds: readonly string[]): string {
  const lines = artifactIds.map((artifactId) => `artifact_id=${artifactId}`);
  const trimmed = text.trim();
  if (lines.length === 0) return trimmed;
  return trimmed ? `${trimmed}\n\n${lines.join("\n")}` : lines.join("\n");
}

// ---------------------------------------------------------------------------
// AG-UI → assistant-ui message conversion
// ---------------------------------------------------------------------------

/**
 * Convert one AG-UI `Message` into an assistant-ui `ThreadMessageLike`.
 * AG-UI `role: "tool"` messages (tool results) are skipped; tool results are
 * surfaced via `ToolCallMessagePart.result` on the preceding assistant message.
 */
export function convertAguiMessage(
  message: Message,
  toolResults: ReadonlyMap<string, unknown> = new Map(),
): ThreadMessageLike | null {
  const { id, role } = message;

  if (role === "tool") {
    return null;
  }

  const content: ThreadMessageLike["content"][number][] = [];

  if (typeof message.content === "string") {
    content.push({ type: "text", text: message.content } satisfies TextMessagePart);
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (typeof part === "string") {
        content.push({ type: "text", text: part } satisfies TextMessagePart);
      } else if (
        part !== null &&
        typeof part === "object" &&
        "text" in part &&
        typeof (part as { text: unknown }).text === "string"
      ) {
        content.push({
          type: "text",
          text: (part as { text: string }).text,
        } satisfies TextMessagePart);
      }
    }
  }

  const toolCalls = (message as Message & { toolCalls?: unknown[] }).toolCalls;

  if (Array.isArray(toolCalls)) {
    for (const toolCall of toolCalls) {
      if (toolCall !== null && typeof toolCall === "object") {
        const record = toolCall as {
          id?: unknown;
          name?: unknown;
          toolName?: unknown;
          args?: unknown;
          input?: unknown;
          function?: { name?: unknown; arguments?: unknown };
          result?: unknown;
          status?: unknown;
        };
        const argsText =
          typeof record.function?.arguments === "string"
            ? record.function.arguments
            : JSON.stringify(record.args ?? record.input ?? {});
        let args: ToolCallMessagePart["args"] = (record.args ??
          record.input ??
          {}) as ToolCallMessagePart["args"];
        try {
          args = JSON.parse(argsText) as ToolCallMessagePart["args"];
        } catch {
          // Partial streaming arguments are still useful as raw text.
        }
        const toolCallId = String(record.id ?? record.toolName ?? "");
        content.push({
          type: "tool-call",
          toolCallId,
          toolName: String(record.name ?? record.toolName ?? record.function?.name ?? "tool"),
          args,
          argsText,
          ...(record.result !== undefined
            ? { result: record.result }
            : toolResults.has(toolCallId)
              ? { result: toolResults.get(toolCallId) }
              : {}),
          ...(record.status === "error" ? { isError: true } : {}),
        } as ThreadMessageLike["content"][number]);
      }
    }
  }

  return {
    id: String(id),
    role: role === "user" ? "user" : "assistant",
    content: content as ThreadMessageLike["content"],
  };
}

function convertAguiMessages(messages: readonly Message[]): ThreadMessageLike[] {
  const results = new Map<string, unknown>();
  for (const message of messages) {
    if (message.role === "tool") {
      const record = message as Message & { toolCallId?: unknown };
      if (typeof record.toolCallId === "string") results.set(record.toolCallId, message.content);
    }
  }
  return messages
    .map((message) => convertAguiMessage(message, results))
    .filter((item): item is ThreadMessageLike => item !== null);
}

// ---------------------------------------------------------------------------
// Runtime state hook
// ---------------------------------------------------------------------------

type HistoryMessage = {
  id: string;
  role: string;
  content: string;
  created_at: string;
};

type HistoryToolCall = {
  id: string;
  tool_name: string;
  args: Record<string, unknown>;
  status: string;
  after_message_id: string;
  result?: string | null;
};

type ThreadHistory = {
  thread_id: string;
  agent_id: string;
  messages: HistoryMessage[];
  tool_calls: HistoryToolCall[];
  latest_run: { id: string; status: string } | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseThreadHistory(value: unknown): ThreadHistory | null {
  if (
    !isRecord(value) ||
    typeof value.thread_id !== "string" ||
    typeof value.agent_id !== "string" ||
    !Array.isArray(value.messages) ||
    !Array.isArray(value.tool_calls)
  ) {
    return null;
  }

  const messages: HistoryMessage[] = [];
  for (const item of value.messages) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.role !== "string" ||
      typeof item.content !== "string" ||
      typeof item.created_at !== "string"
    ) {
      return null;
    }
    messages.push({
      id: item.id,
      role: item.role,
      content: item.content,
      created_at: item.created_at,
    });
  }

  const tool_calls: HistoryToolCall[] = [];
  for (const item of value.tool_calls) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.tool_name !== "string" ||
      !isRecord(item.args) ||
      typeof item.status !== "string" ||
      typeof item.after_message_id !== "string"
    ) {
      return null;
    }
    tool_calls.push({
      id: item.id,
      tool_name: item.tool_name,
      args: item.args,
      status: item.status,
      after_message_id: item.after_message_id,
      result: typeof item.result === "string" ? item.result : null,
    });
  }

  let latest_run: ThreadHistory["latest_run"] = null;
  if (value.latest_run !== null && value.latest_run !== undefined) {
    if (
      !isRecord(value.latest_run) ||
      typeof value.latest_run.id !== "string" ||
      typeof value.latest_run.status !== "string"
    ) {
      return null;
    }
    latest_run = { id: value.latest_run.id, status: value.latest_run.status };
  }

  return {
    thread_id: value.thread_id,
    agent_id: value.agent_id,
    messages,
    tool_calls,
    latest_run,
  };
}

function historyToAgentMessages(history: ThreadHistory): Message[] {
  // The AG-UI request contract accepts only its own message schema. Historical
  // tool cards are a display projection and must never be echoed to the API.
  return history.messages.map(
    (message) => ({ id: message.id, role: message.role, content: message.content }) as Message,
  );
}

function historyToDisplayMessages(history: ThreadHistory): Message[] {
  const callsByAssistantMessageId = new Map<string, HistoryToolCall[]>();
  const unpairedCallsByUserMessageId = new Map<string, HistoryToolCall[]>();

  for (const toolCall of history.tool_calls) {
    const userIndex = history.messages.findIndex(
      (message) => message.id === toolCall.after_message_id,
    );
    const followingAssistant = history.messages
      .slice(userIndex + 1)
      .find((message) => message.role === "assistant" || message.role === "user");

    if (followingAssistant?.role === "assistant") {
      const calls = callsByAssistantMessageId.get(followingAssistant.id) ?? [];
      calls.push(toolCall);
      callsByAssistantMessageId.set(followingAssistant.id, calls);
      continue;
    }

    const calls = unpairedCallsByUserMessageId.get(toolCall.after_message_id) ?? [];
    calls.push(toolCall);
    unpairedCallsByUserMessageId.set(toolCall.after_message_id, calls);
  }

  const toToolCalls = (toolCalls: readonly HistoryToolCall[]) =>
    toolCalls.map((toolCall) => ({
      id: toolCall.id,
      name: toolCall.tool_name,
      args: toolCall.args,
      result: toolCall.result,
      status: toolCall.status,
    }));

  return history.messages.flatMap((message) => {
    const toolCalls = callsByAssistantMessageId.get(message.id);
    const converted: Message = {
      id: message.id,
      role: message.role,
      content: message.content,
      ...(toolCalls === undefined ? {} : { toolCalls: toToolCalls(toolCalls) }),
    } as Message;

    const unpairedCalls = unpairedCallsByUserMessageId.get(message.id);
    if (message.role !== "user" || unpairedCalls === undefined) {
      return [converted];
    }

    // `after_message_id` deliberately anchors to the user turn in storage.
    // assistant-ui accepts tool-call parts only on assistant messages.
    return [
      converted,
      {
        id: `history-tools-${message.id}`,
        role: "assistant",
        content: "",
        toolCalls: toToolCalls(unpairedCalls),
      } as unknown as Message,
    ];
  });
}

function createAgent(
  threadId: string,
  initialMessages: Message[],
  agentId: string | null,
): HttpAgent {
  return new HttpAgent({
    url: "/api/ag-ui/runs",
    threadId,
    initialMessages,
    headers: agentId === null ? {} : { "X-AgentOS-Agent-Id": agentId },
  });
}

export type AguiRuntimeOptions = {
  selectedThreadId: string | null | undefined;
  agentId: string | null;
  onStreamingChanged?: (isStreaming: boolean) => void;
  onThreadChanged?: (threadId: string | null, agentId?: string) => void;
  onRunFinalized?: () => void;
  onRunStarted?: (runId: string) => void;
};

/**
 * Bridge AG-UI `HttpAgent` events into assistant-ui external-store shape.
 * The hook owns `messages` + `isRunning`; `onNew` drives a run through
 * `HttpAgent.runAgent(undefined, subscriber)` and streams events back.
 */
export function useAguiRuntime({
  selectedThreadId,
  agentId,
  onStreamingChanged,
  onThreadChanged,
  onRunFinalized,
  onRunStarted,
}: AguiRuntimeOptions) {
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const agentRef = useRef<HttpAgent | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const lastRunIdRef = useRef<string | null>(null);
  const latestThreadIdRef = useRef<string | null>(selectedThreadId ?? null);
  const artifactIdsRef = useRef(new Map<string, string>());
  const recoveryInFlightRef = useRef(false);
  const recoverRunRef = useRef<((runId: string) => Promise<void>) | null>(null);
  const callbacksRef = useRef({
    onStreamingChanged,
    onThreadChanged,
    onRunFinalized,
    onRunStarted,
  });
  const [historyVersion, setHistoryVersion] = useState(0);

  useEffect(() => {
    callbacksRef.current = {
      onStreamingChanged,
      onThreadChanged,
      onRunFinalized,
      onRunStarted,
    };
  }, [onRunFinalized, onRunStarted, onStreamingChanged, onThreadChanged]);

  const refreshHistory = useCallback(() => {
    setHistoryVersion((current) => current + 1);
  }, []);

  const ensureThreadForUpload = useCallback(async (): Promise<string> => {
    const currentThreadId = latestThreadIdRef.current;
    if (currentThreadId !== null) {
      return currentThreadId;
    }

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
      throw new Error("无法创建会话，请稍后重试后再上传文件。");
    }

    const threadId = payload.id;
    const threadAgentId =
      typeof payload.agent_id === "string" && isUuid(payload.agent_id) ? payload.agent_id : agentId;
    latestThreadIdRef.current = threadId;
    agentRef.current = createAgent(threadId, [], threadAgentId);
    callbacksRef.current.onThreadChanged?.(threadId, threadAgentId ?? undefined);
    return threadId;
  }, [agentId]);

  const attachmentAdapter = useMemo<AttachmentAdapter>(
    () => ({
      accept: UPLOAD_ACCEPT,
      async add({ file }): Promise<PendingAttachment> {
        return {
          id: crypto.randomUUID(),
          type: file.type.startsWith("image/") ? "image" : "document",
          name: file.name,
          contentType: file.type,
          file,
          status: { type: "requires-action", reason: "composer-send" },
        };
      },
      async send(attachment): Promise<CompleteAttachment> {
        const threadId = await ensureThreadForUpload();
        const formData = new FormData();
        formData.append("file", attachment.file);
        formData.append("thread_id", threadId);
        const response = await fetch("/api/uploads", { method: "POST", body: formData });
        const payload: unknown = await response.json().catch(() => null);
        if (
          !response.ok ||
          !isRecord(payload) ||
          typeof payload.artifact_id !== "string" ||
          !isUuid(payload.artifact_id)
        ) {
          throw new Error("附件上传失败，请检查格式或文件大小后重试。");
        }
        artifactIdsRef.current.set(attachment.id, payload.artifact_id);
        return {
          ...attachment,
          status: { type: "complete" },
          content: [{ type: "text", text: `artifact_id=${payload.artifact_id}` }],
        };
      },
      async remove(attachment) {
        artifactIdsRef.current.delete(attachment.id);
      },
    }),
    [ensureThreadForUpload],
  );

  // Notify listeners when streaming state flips.
  useEffect(() => {
    callbacksRef.current.onStreamingChanged?.(isRunning);
  }, [isRunning]);

  // The selected thread is the runtime's source of truth. Load durable history
  // before allowing the composer to send, and replace the agent so its internal
  // messages match the visible assistant-ui messages.
  useEffect(() => {
    const controller = new AbortController();
    let current = true;

    if (selectedThreadId === null || selectedThreadId === undefined) {
      latestThreadIdRef.current = null;
      agentRef.current = createAgent("new", [], agentId);
      queueMicrotask(() => {
        if (current) {
          setMessages([]);
          setIsRunning(false);
          setIsLoading(false);
        }
      });
      return () => controller.abort();
    }

    latestThreadIdRef.current = selectedThreadId;
    queueMicrotask(() => {
      if (current) {
        setIsLoading(true);
        setIsRunning(false);
        setMessages([]);
      }
    });

    void (async () => {
      try {
        const response = await fetch(`/api/threads/${selectedThreadId}/messages`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`无法读取会话历史（${response.status}）`);
        }
        const history = parseThreadHistory((await response.json()) as unknown);
        if (history === null || history.thread_id !== selectedThreadId) {
          throw new Error("会话历史格式无效");
        }

        const agentMessages = historyToAgentMessages(history);
        const displayMessages = historyToDisplayMessages(history);
        if (!current) {
          return;
        }
        agentRef.current = createAgent(history.thread_id, agentMessages, agentId);
        latestThreadIdRef.current = history.thread_id;
        setMessages(convertAguiMessages(displayMessages));
        callbacksRef.current.onThreadChanged?.(history.thread_id, history.agent_id);
        if (history.latest_run !== null) {
          lastRunIdRef.current = history.latest_run.id;
          callbacksRef.current.onRunStarted?.(history.latest_run.id);
          setIsRunning(isActiveRunStatus(history.latest_run.status));
        }
      } catch {
        if (current && !controller.signal.aborted) {
          agentRef.current = createAgent(selectedThreadId, [], agentId);
        }
      } finally {
        if (current) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      current = false;
      controller.abort();
    };
  }, [agentId, historyVersion, selectedThreadId]);

  const send = useCallback(
    async (text: string, artifactIds: readonly string[] = []) => {
      const agent = agentRef.current;
      if (agent === null) {
        return;
      }

      const content = buildMessageWithArtifacts(text, artifactIds);
      if (!content) {
        return;
      }

      // Optimistic user message.
      const userMessageId = crypto.randomUUID();
      const userMessage: ThreadMessageLike = {
        id: userMessageId,
        role: "user",
        content: [{ type: "text", text: content } satisfies TextMessagePart],
        createdAt: new Date(),
      };

      // HttpAgent builds the request from its own `messages` array. Keep it in
      // sync with the optimistic assistant-ui message before starting the run;
      // otherwise the API receives an empty/non-user final message (HTTP 422).
      agent.addMessage({
        id: userMessageId,
        role: "user",
        content,
      });
      setMessages((current) => [...current, userMessage]);
      setIsRunning(true);

      try {
        await agent.runAgent(undefined, {
          onRunStartedEvent: ({ event }) => {
            activeRunIdRef.current = event.runId;
            lastRunIdRef.current = event.runId;
            callbacksRef.current.onRunStarted?.(event.runId);
            if (event.threadId !== undefined) {
              agent.threadId = event.threadId;
              latestThreadIdRef.current = event.threadId;
              callbacksRef.current.onThreadChanged?.(event.threadId);
            }
          },
          onMessagesChanged: ({ messages: nextMessages }) => {
            setMessages(convertAguiMessages(nextMessages));
          },
          onRunErrorEvent: () => {
            // Errors surface through the message snapshot; no extra handling needed.
          },
          onRunFinishedEvent: () => {
            activeRunIdRef.current = null;
            setIsRunning(false);
            callbacksRef.current.onRunFinalized?.();
          },
        });
      } catch {
        const runId = activeRunIdRef.current;
        if (runId === null) {
          setIsRunning(false);
          callbacksRef.current.onRunFinalized?.();
          return;
        }
        // The server run outlives the SSE response. Preserve that contract on
        // mobile/background disconnects by recovering from durable Run state.
        void recoverRunRef.current?.(runId);
      }
    },
    [refreshHistory],
  );

  // HITL resume: consume `/api/runs/{runId}/stream` and merge into the same store.
  const resumeRun = useCallback(
    async (runId: string, anchorMessageId: string): Promise<boolean> => {
      activeRunIdRef.current = runId;
      setIsRunning(true);
      try {
        const response = await fetch(`/api/runs/${runId}/stream`, { cache: "no-store" });
        if (response.status !== 200 || response.body === null) {
          return false;
        }

        const reasoningBuffers = new Map<string, string>();
        const textBuffers = new Map<string, string>();
        let sawTerminal = false;

        await readAguiEventStream(response.body, (event: AguiStreamEvent) => {
          switch (event.type) {
            case "REASONING_START": {
              if (event.messageId !== undefined) {
                reasoningBuffers.set(event.messageId, "");
              }
              break;
            }
            case "REASONING_MESSAGE_CONTENT": {
              const messageId = event.messageId;
              if (messageId !== undefined) {
                reasoningBuffers.set(
                  messageId,
                  (reasoningBuffers.get(messageId) ?? "") + (event.delta ?? ""),
                );
                const buffer = reasoningBuffers.get(messageId) ?? "";
                setMessages((current) => {
                  const index =
                    anchorMessageId === ""
                      ? current.length - 1
                      : current.findIndex((m) => m.id === anchorMessageId);
                  if (index === -1) {
                    return [
                      ...current,
                      {
                        id: messageId,
                        role: "assistant",
                        content: [
                          { type: "reasoning", text: buffer } satisfies ReasoningMessagePart,
                        ],
                      },
                    ];
                  }
                  const next = [...current];
                  const existing = next[index];
                  if (existing === undefined) {
                    return current;
                  }
                  const rawContent = existing.content;
                  const existingContent = Array.isArray(rawContent)
                    ? rawContent
                    : [{ type: "text", text: rawContent as string } satisfies TextMessagePart];
                  const content = existingContent.filter(
                    (part): part is Exclude<ThreadMessageLike["content"][number], string> =>
                      typeof part !== "string" && (part as { type?: string }).type !== "reasoning",
                  );
                  content.push({
                    type: "reasoning",
                    text: buffer,
                  } satisfies ReasoningMessagePart);
                  next[index] = { ...existing, content };
                  return next;
                });
              }
              break;
            }
            case "TOOL_CALL_ARGS": {
              const toolCallId = event.toolCallId;
              if (toolCallId !== undefined) {
                setMessages((current) => {
                  const index = current.length - 1;
                  const existing = index < 0 ? undefined : current[index];
                  const part: ToolCallMessagePart = {
                    type: "tool-call",
                    toolCallId,
                    toolName: event.toolCallName ?? "tool",
                    args: {},
                    argsText: event.delta ?? "",
                  };
                  if (existing?.role !== "assistant") {
                    return [
                      ...current,
                      { id: `resume-${toolCallId}`, role: "assistant", content: [part] },
                    ];
                  }
                  const content = Array.isArray(existing.content) ? [...existing.content] : [];
                  const partIndex = content.findIndex(
                    (item) =>
                      typeof item !== "string" &&
                      item.type === "tool-call" &&
                      item.toolCallId === toolCallId,
                  );
                  if (partIndex === -1) content.push(part);
                  else {
                    const previous = content[partIndex] as ToolCallMessagePart;
                    content[partIndex] = {
                      ...previous,
                      argsText: `${previous.argsText ?? ""}${event.delta ?? ""}`,
                    };
                  }
                  const next = [...current];
                  next[index] = { ...existing, content };
                  return next;
                });
              }
              break;
            }
            case "TEXT_MESSAGE_START": {
              const messageId = event.messageId;
              if (messageId !== undefined) {
                textBuffers.set(messageId, "");
                setMessages((current) => [
                  ...current,
                  { id: messageId, role: "assistant", content: [{ type: "text", text: "" }] },
                ]);
              }
              break;
            }
            case "TEXT_MESSAGE_CONTENT": {
              const messageId = event.messageId;
              if (messageId !== undefined) {
                const text = (textBuffers.get(messageId) ?? "") + (event.delta ?? "");
                textBuffers.set(messageId, text);
                setMessages((current) =>
                  current.map((message) =>
                    message.id === messageId
                      ? { ...message, content: [{ type: "text", text } satisfies TextMessagePart] }
                      : message,
                  ),
                );
              }
              break;
            }
            case "RUN_ERROR": {
              sawTerminal = true;
              break;
            }
            case "RUN_FINISHED": {
              sawTerminal = true;
              break;
            }
            default:
              break;
          }
        });

        setIsRunning(false);
        activeRunIdRef.current = null;
        refreshHistory();
        callbacksRef.current.onRunFinalized?.();
        return sawTerminal;
      } catch {
        setIsRunning(false);
        callbacksRef.current.onRunFinalized?.();
        return false;
      }
    },
    [refreshHistory],
  );

  const recoverRun = useCallback(
    async (runId: string) => {
      if (recoveryInFlightRef.current) return;
      recoveryInFlightRef.current = true;
      setIsRunning(true);
      try {
        for (let attempt = 0; attempt < RECOVERY_MAX_ATTEMPTS; attempt += 1) {
          const response = await fetch(`/api/runs/${runId}`, { cache: "no-store" });
          const state: unknown = response.ok ? await response.json().catch(() => null) : null;
          const status = isRecord(state) && typeof state.status === "string" ? state.status : null;
          if (status === "waiting_approval" || (status !== null && !isActiveRunStatus(status))) {
            refreshHistory();
            return;
          }
          const delay = RECOVERY_DELAYS_MS[attempt] ?? RECOVERY_DELAYS_MS.at(-1)!;
          await new Promise<void>((resolve) => window.setTimeout(resolve, delay));
        }
      } finally {
        activeRunIdRef.current = null;
        setIsRunning(false);
        refreshHistory();
        callbacksRef.current.onRunFinalized?.();
        recoveryInFlightRef.current = false;
      }
    },
    [refreshHistory],
  );

  useEffect(() => {
    recoverRunRef.current = recoverRun;
  }, [recoverRun]);

  // The `onNew` handler assistant-ui calls when the user submits the composer.
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = message.content.find((part) => part.type === "text")?.text ?? "";
      const artifactIds = (message.attachments ?? [])
        .map((attachment) => artifactIdsRef.current.get(attachment.id))
        .filter((artifactId): artifactId is string => artifactId !== undefined);
      if (text.trim() === "" && artifactIds.length === 0) {
        return;
      }
      await send(text, artifactIds);
    },
    [send],
  );

  return {
    messages,
    isRunning,
    isLoading,
    historyVersion,
    onNew,
    resumeRun,
    refreshHistory,
    attachmentAdapter,
    sendText: async (text: string) => send(text),
    cancelRun: async () => {
      const runId = activeRunIdRef.current;
      if (runId !== null) {
        await fetch(`/api/runs/${runId}/cancel`, { method: "POST", keepalive: true });
      }
      agentRef.current?.abortRun();
    },
  };
}
