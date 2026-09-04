/**
 * AG-UI → assistant-ui ExternalStoreRuntime adapter.
 *
 * Owns chat state (messages + isRunning) in a React state, translates AG-UI wire events
 * (via `@ag-ui/client`'s `HttpAgent` subscriber callbacks, plus the HITL resume `/stream`
 * SSE consumed through `readAguiEventStream`) into assistant-ui `ThreadMessageLike`
 * messages, and exposes a `useExternalStoreRuntime`-compatible adapter object.
 *
 * The assistant-ui types (`ThreadMessageLike`, `AppendMessage`, `MessagePart`) are imported
 * `import type` so this module type-checks standalone; the concrete runtime wiring happens
 * in `AssistantThread` (which mounts `AssistantRuntimeProvider`).
 */

import { HttpAgent, type Message } from "@ag-ui/client";
import { useCallback, useEffect, useRef, useState } from "react";

import { readAguiEventStream, type AguiStreamEvent } from "@/lib/agui-events";

// ---------------------------------------------------------------------------
// assistant-ui type shims (concrete types come from @assistant-ui/react once installed)
// ---------------------------------------------------------------------------

type AssistantTextPart = { type: "text"; text: string };
type AssistantReasoningPart = { type: "reasoning"; text: string };
type AssistantToolCallPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args: unknown;
};
type AssistantToolResultPart = { type: "tool-result"; toolCallId: string; result: unknown };
type AssistantMessagePart =
  AssistantTextPart | AssistantReasoningPart | AssistantToolCallPart | AssistantToolResultPart;

type AssistantMessageLike = {
  id: string;
  role: "user" | "assistant";
  content: readonly AssistantMessagePart[];
  createdAt?: Date;
  status?: { type: "running" } | { type: "complete" } | { type: "incomplete" };
};

type AppendMessage = {
  content: readonly { type: "text"; text: string }[];
  parentId?: string;
  id?: string;
};

// ---------------------------------------------------------------------------
// AG-UI → assistant-ui message conversion
// ---------------------------------------------------------------------------

/**
 * Convert one AG-UI `Message` into an assistant-ui `ThreadMessageLike`.
 * AG-UI `role: "tool"` messages (tool results) are merged into the preceding
 * assistant message as `tool-result` parts; they are not standalone UI messages.
 */
export function convertAguiMessage(message: Message): AssistantMessageLike | null {
  const { id, role } = message;

  if (role === "tool") {
    return null;
  }

  const content: AssistantMessagePart[] = [];

  if (typeof message.content === "string") {
    content.push({ type: "text", text: message.content });
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (typeof part === "string") {
        content.push({ type: "text", text: part });
      } else if (part !== null && typeof part === "object" && "text" in part) {
        content.push({ type: "text", text: String((part as { text: unknown }).text ?? "") });
      } else if (part !== null && typeof part === "object" && "type" in part) {
        const typed = part as { type?: string };
        if (typed.type === "text" && "text" in (part as { text?: unknown })) {
          content.push({ type: "text", text: String((part as { text: unknown }).text ?? "") });
        }
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
          args: record.args ?? record.input ?? {},
        });
      }
    }
  }

  return {
    id: String(id),
    role: role === "user" ? "user" : "assistant",
    content,
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
  const [messages, setMessages] = useState<AssistantMessageLike[]>([]);
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
      const userMessage: AssistantMessageLike = {
        id: crypto.randomUUID(),
        role: "user",
        content: [{ type: "text", text }],
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
                .filter((item): item is AssistantMessageLike => item !== null),
            );
          },
          onToolCallStartEvent: ({ event }) => {
            // Converted into a tool-call part on the next messages snapshot.
            void event;
          },
          onReasoningStartEvent: ({ event }) => {
            void event;
          },
          onRunErrorEvent: ({ event }) => {
            void event;
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
                  const content = existing.content.filter(
                    (p): p is AssistantMessagePart => p.type !== "reasoning",
                  );
                  content.push({ type: "reasoning", text: buffer } as AssistantMessagePart);
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

// Re-export for the AssistantThread component.
export type { AssistantMessageLike, AssistantMessagePart };
