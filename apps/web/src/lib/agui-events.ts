/**
 * AG-UI wire event parsing for the assistant-ui migration.
 *
 * Extracted from `apps/web/src/components/chat/chat-panel.tsx` (the `ResumeStreamEvent`
 * type and the event-switch inside `streamResumedRun`) into a standalone module so the
 * `ExternalStoreRuntime` adapter can consume both the `HttpAgent` callback stream and the
 * HITL resume `/stream` SSE from a single source of truth.
 *
 * The AG-UI SSE stream is JSON lines behind `data:` markers, frame-delimited by `\n\n`.
 * Events are camelCase with `exclude_none` serialization — every field except `type`
 * may be absent.
 */

/** Raw AG-UI wire event as parsed from the SSE stream. */
export type AguiStreamEvent = {
  type?: string;
  messageId?: string;
  toolCallId?: string;
  toolCallName?: string;
  delta?: string;
  content?: string;
  subtype?: string;
  message?: string;
};

/**
 * Known AG-UI event type names. Kept as a union of string literals so the adapter can
 * switch exhaustively; unknown event types fall through to the catch-all `parseAguiEvent`.
 */
export type AguiEventType =
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "TOOL_CALL_RESULT"
  | "REASONING_START"
  | "REASONING_MESSAGE_START"
  | "REASONING_MESSAGE_CONTENT"
  | "REASONING_MESSAGE_END"
  | "REASONING_ENCRYPTED_VALUE"
  | "REASONING_END"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "RUN_ERROR"
  | "RUN_FINISHED";

export const AGUI_EVENT_TYPES: ReadonlySet<string> = new Set<AguiEventType>([
  "TOOL_CALL_START",
  "TOOL_CALL_ARGS",
  "TOOL_CALL_END",
  "TOOL_CALL_RESULT",
  "REASONING_START",
  "REASONING_MESSAGE_START",
  "REASONING_MESSAGE_CONTENT",
  "REASONING_MESSAGE_END",
  "REASONING_ENCRYPTED_VALUE",
  "REASONING_END",
  "TEXT_MESSAGE_START",
  "TEXT_MESSAGE_CONTENT",
  "RUN_ERROR",
  "RUN_FINISHED",
]);

/** Parse one raw SSE `data:` payload into a typed event; returns null when invalid. */
export function parseAguiEvent(raw: string): AguiStreamEvent | null {
  if (raw === "") {
    return null;
  }

  try {
    const value: unknown = JSON.parse(raw);

    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      return null;
    }

    const record = value as Record<string, unknown>;
    const type = typeof record.type === "string" ? record.type : undefined;

    // Unknown/absent type: still hand it through so the adapter can ignore gracefully.
    return {
      type,
      messageId: typeof record.messageId === "string" ? record.messageId : undefined,
      toolCallId: typeof record.toolCallId === "string" ? record.toolCallId : undefined,
      toolCallName: typeof record.toolCallName === "string" ? record.toolCallName : undefined,
      delta: typeof record.delta === "string" ? record.delta : undefined,
      content: typeof record.content === "string" ? record.content : undefined,
      subtype: typeof record.subtype === "string" ? record.subtype : undefined,
      message: typeof record.message === "string" ? record.message : undefined,
    };
  } catch {
    return null;
  }
}

/**
 * Split an SSE byte stream into frames and emit one parsed event per `data:` line.
 * `onEvent` receives each parsed event; the loop drives the reader until EOF or abort.
 */
export async function readAguiEventStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: AguiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = "";

  try {
    for (;;) {
      if (signal?.aborted) {
        return;
      }

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

          const event = parseAguiEvent(raw);

          if (event !== null) {
            onEvent(event);
          }
        }

        separator = pending.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
