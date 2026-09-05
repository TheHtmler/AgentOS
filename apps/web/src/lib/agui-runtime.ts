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
  AppendMessage,
  ReasoningMessagePart,
  TextMessagePart,
  ThreadMessageLike,
  ToolCallMessagePart,
} from "@assistant-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { readAguiEventStream, type AguiStreamEvent } from "@/lib/agui-events";
import { isActiveRunStatus } from "@/lib/run-recovery";

// ---------------------------------------------------------------------------
// AG-UI → assistant-ui message conversion
// ---------------------------------------------------------------------------

/**
 * Convert one AG-UI `Message` into an assistant-ui `ThreadMessageLike`.
 * AG-UI `role: "tool"` messages (tool results) are skipped; tool results are
 * surfaced via `ToolCallMessagePart.result` on the preceding assistant message.
 */
export function convertAguiMessage(message: Message): ThreadMessageLike | null {
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
          result?: unknown;
          status?: unknown;
        };
        content.push({
          type: "tool-call",
          toolCallId: String(record.id ?? record.toolName ?? ""),
          toolName: String(record.name ?? record.toolName ?? "tool"),
          args: (record.args ?? record.input ?? {}) as ToolCallMessagePart["args"],
          argsText: JSON.stringify(record.args ?? record.input ?? {}),
          ...(record.result !== undefined ? { result: record.result } : {}),
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

function historyToAguiMessages(history: ThreadHistory): Message[] {
  const callsByMessageId = new Map<string, HistoryToolCall[]>();
  for (const toolCall of history.tool_calls) {
    const calls = callsByMessageId.get(toolCall.after_message_id) ?? [];
    calls.push(toolCall);
    callsByMessageId.set(toolCall.after_message_id, calls);
  }

  return history.messages.map((message) => {
    const toolCalls = callsByMessageId.get(message.id);
    return {
      id: message.id,
      role: message.role,
      content: message.content,
      ...(toolCalls === undefined
        ? {}
        : {
            toolCalls: toolCalls.map((toolCall) => ({
              id: toolCall.id,
              name: toolCall.tool_name,
              args: toolCall.args,
              result: toolCall.result,
              status: toolCall.status,
            })),
          }),
    } as Message;
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

  // Notify listeners when streaming state flips.
  useEffect(() => {
    onStreamingChanged?.(isRunning);
  }, [isRunning, onStreamingChanged]);

  // The selected thread is the runtime's source of truth. Load durable history
  // before allowing the composer to send, and replace the agent so its internal
  // messages match the visible assistant-ui messages.
  useEffect(() => {
    const controller = new AbortController();
    let current = true;

    setIsLoading(selectedThreadId !== null && selectedThreadId !== undefined);
    setIsRunning(false);
    setMessages([]);

    if (selectedThreadId === null || selectedThreadId === undefined) {
      agentRef.current = createAgent("new", [], agentId);
      setIsLoading(false);
      return () => controller.abort();
    }

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

        const aguiMessages = historyToAguiMessages(history);
        if (!current) {
          return;
        }
        agentRef.current = createAgent(history.thread_id, aguiMessages, agentId);
        setMessages(
          aguiMessages
            .map(convertAguiMessage)
            .filter((item): item is ThreadMessageLike => item !== null),
        );
        onThreadChanged?.(history.thread_id, history.agent_id);
        if (history.latest_run !== null) {
          onRunStarted?.(history.latest_run.id);
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
  }, [agentId, onRunStarted, onThreadChanged, selectedThreadId]);

  const send = useCallback(
    async (text: string) => {
      const agent = agentRef.current;
      if (agent === null) {
        return;
      }

      // Optimistic user message.
      const userMessageId = crypto.randomUUID();
      const userMessage: ThreadMessageLike = {
        id: userMessageId,
        role: "user",
        content: [{ type: "text", text } satisfies TextMessagePart],
        createdAt: new Date(),
      };

      // HttpAgent builds the request from its own `messages` array. Keep it in
      // sync with the optimistic assistant-ui message before starting the run;
      // otherwise the API receives an empty/non-user final message (HTTP 422).
      agent.addMessage({
        id: userMessageId,
        role: "user",
        content: text,
      });
      setMessages((current) => [...current, userMessage]);
      setIsRunning(true);

      try {
        await agent.runAgent(undefined, {
          onRunStartedEvent: ({ event }) => {
            onRunStarted?.(event.runId);
            if (event.threadId !== undefined) {
              agent.threadId = event.threadId;
              onThreadChanged?.(event.threadId);
            }
          },
          onMessagesChanged: ({ messages: nextMessages }) => {
            setMessages(
              nextMessages
                .map(convertAguiMessage)
                .filter((item): item is ThreadMessageLike => item !== null),
            );
          },
          onRunErrorEvent: () => {
            // Errors surface through the message snapshot; no extra handling needed.
          },
          onRunFinishedEvent: () => {
            setIsRunning(false);
            onRunFinalized?.();
          },
        });
      } catch {
        setIsRunning(false);
        onRunFinalized?.();
      }
    },
    [onRunFinalized, onRunStarted, onThreadChanged],
  );

  // HITL resume: consume `/api/runs/{runId}/stream` and merge into the same store.
  const resumeRun = useCallback(
    async (runId: string, anchorMessageId: string): Promise<boolean> => {
      try {
        const response = await fetch(`/api/runs/${runId}/stream`, { cache: "no-store" });
        if (response.status !== 200 || response.body === null) {
          return false;
        }

        const reasoningBuffers = new Map<string, string>();
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
                  const index = current.findIndex((m) => m.id === anchorMessageId);
                  if (index === -1) {
                    return current;
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
        onRunFinalized?.();
        return sawTerminal;
      } catch {
        setIsRunning(false);
        onRunFinalized?.();
        return false;
      }
    },
    [onRunFinalized],
  );

  // The `onNew` handler assistant-ui calls when the user submits the composer.
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = message.content.find((part) => part.type === "text")?.text ?? "";
      if (text.trim() === "") {
        return;
      }
      await send(text);
    },
    [send],
  );

  return {
    messages,
    isRunning,
    isLoading,
    onNew,
    resumeRun,
  };
}
