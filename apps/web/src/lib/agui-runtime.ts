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
        };
        content.push({
          type: "tool-call",
          toolCallId: String(record.id ?? record.toolName ?? ""),
          toolName: String(record.name ?? record.toolName ?? "tool"),
          args: (record.args ?? record.input ?? {}) as ToolCallMessagePart["args"],
          argsText: JSON.stringify(record.args ?? record.input ?? {}),
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

export type AguiRuntimeOptions = {
  agentId: string | null;
  onStreamingChanged?: (isStreaming: boolean) => void;
  onThreadChanged?: (threadId: string | null) => void;
  onRunFinalized?: () => void;
  onRunStarted?: (runId: string) => void;
};

/**
 * Bridge AG-UI `HttpAgent` events into assistant-ui external-store shape.
 * The hook owns `messages` + `isRunning`; `onNew` drives a run through
 * `HttpAgent.runAgent(undefined, subscriber)` and streams events back.
 */
export function useAguiRuntime({
  agentId,
  onStreamingChanged,
  onThreadChanged,
  onRunFinalized,
  onRunStarted,
}: AguiRuntimeOptions) {
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const agentRef = useRef<HttpAgent | null>(null);

  // Notify listeners when streaming state flips.
  useEffect(() => {
    onStreamingChanged?.(isRunning);
  }, [isRunning, onStreamingChanged]);

  // The HttpAgent instance is created once; thread changes re-create it lazily.
  if (agentRef.current === null) {
    agentRef.current = new HttpAgent({
      url: "/api/ag-ui/runs",
      threadId: "new",
      headers: agentId === null ? {} : { "X-AgentOS-Agent-Id": agentId },
    });
  }

  const send = useCallback(
    async (text: string) => {
      const agent = agentRef.current;
      if (agent === null) {
        return;
      }

      // Optimistic user message.
      const userMessage: ThreadMessageLike = {
        id: crypto.randomUUID(),
        role: "user",
        content: [{ type: "text", text } satisfies TextMessagePart],
        createdAt: new Date(),
      };

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
    onNew,
    resumeRun,
  };
}
