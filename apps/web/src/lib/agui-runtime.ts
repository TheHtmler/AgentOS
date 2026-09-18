/** AgentOS 持久化历史到 AG-UI 展示消息的纯投影；运行状态由官方 runtime 管理。 */

import type { Message } from "@ag-ui/client";
import type { ThreadMessageLike, ToolCallMessagePart } from "@assistant-ui/react";

export type HistoryAttachment = { id: string; title: string; mime_type: string };
type HistoryMessage = {
  id: string;
  role: string;
  content: string;
  created_at: string;
  attachments: HistoryAttachment[];
};
type HistoryToolCall = {
  id: string;
  tool_name: string;
  args: Record<string, unknown>;
  status: string;
  after_message_id: string;
  result?: string | null;
};
export type ThreadHistory = {
  thread_id: string;
  agent_id: string;
  messages: HistoryMessage[];
  tool_calls: HistoryToolCall[];
  latest_run: { id: string; status: string } | null;
};

type HistoryDisplayMessage = Message & { uploadAttachments?: HistoryAttachment[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

/** 把 owner-scoped 404 解释为会话已不可访问，其余响应继续按错误处理。 */
export async function readThreadHistoryResponse(response: Response): Promise<unknown | null> {
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`无法读取会话历史（${response.status}）`);
  return (await response.json()) as unknown;
}

export function parseThreadHistory(value: unknown): ThreadHistory | null {
  if (
    !isRecord(value) ||
    typeof value.thread_id !== "string" ||
    typeof value.agent_id !== "string" ||
    !Array.isArray(value.messages) ||
    !Array.isArray(value.tool_calls)
  )
    return null;

  const messages: HistoryMessage[] = [];
  for (const item of value.messages) {
    if (
      !isRecord(item) ||
      typeof item.id !== "string" ||
      typeof item.role !== "string" ||
      typeof item.content !== "string" ||
      typeof item.created_at !== "string"
    )
      return null;
    const attachments: HistoryAttachment[] = [];
    if (item.attachments !== undefined) {
      if (!Array.isArray(item.attachments)) return null;
      for (const attachment of item.attachments) {
        if (
          !isRecord(attachment) ||
          typeof attachment.id !== "string" ||
          !isUuid(attachment.id) ||
          typeof attachment.title !== "string" ||
          typeof attachment.mime_type !== "string"
        )
          return null;
        attachments.push({
          id: attachment.id,
          title: attachment.title,
          mime_type: attachment.mime_type,
        });
      }
    }
    messages.push({
      id: item.id,
      role: item.role,
      content: item.content,
      created_at: item.created_at,
      attachments,
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
    )
      return null;
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
    )
      return null;
    latest_run = { id: value.latest_run.id, status: value.latest_run.status };
  }
  return { thread_id: value.thread_id, agent_id: value.agent_id, messages, tool_calls, latest_run };
}

export function historyToDisplayMessages(history: Pick<ThreadHistory, "messages" | "tool_calls">) {
  const callsByAssistant = new Map<string, HistoryToolCall[]>();
  const callsByUser = new Map<string, HistoryToolCall[]>();
  for (const call of history.tool_calls) {
    const userIndex = history.messages.findIndex((message) => message.id === call.after_message_id);
    const assistant = history.messages
      .slice(userIndex + 1)
      .find((message) => message.role === "assistant" || message.role === "user");
    const target = assistant?.role === "assistant" ? callsByAssistant : callsByUser;
    const key = assistant?.role === "assistant" ? assistant.id : call.after_message_id;
    target.set(key, [...(target.get(key) ?? []), call]);
  }
  const toolMessage = (id: string, calls: HistoryToolCall[]) =>
    ({
      id: `history-tools-${id}`,
      role: "assistant",
      content: "",
      toolCalls: calls.map((call) => ({
        type: "tool-call",
        id: call.id,
        name: call.tool_name,
        args: call.args,
        result: call.result,
        isError: call.status === "error",
      })),
    }) as unknown as HistoryDisplayMessage;

  return history.messages.flatMap((message) => {
    const converted = {
      id: message.id,
      role: message.role,
      content: message.content,
      ...(message.attachments.length ? { uploadAttachments: message.attachments } : {}),
    } as HistoryDisplayMessage;
    const before = callsByAssistant.get(message.id);
    if (before) return [toolMessage(message.id, before), converted];
    const after = callsByUser.get(message.id);
    return message.role === "user" && after
      ? [converted, toolMessage(message.id, after)]
      : [converted];
  });
}

export function convertAguiMessages(messages: readonly Message[]): ThreadMessageLike[] {
  const results = new Map<string, unknown>();
  for (const message of messages) {
    if (message.role === "tool") {
      const toolCallId = (message as Message & { toolCallId?: unknown }).toolCallId;
      if (typeof toolCallId === "string") results.set(toolCallId, message.content);
    }
  }
  return messages.flatMap((message) => {
    if (message.role !== "user" && message.role !== "assistant" && message.role !== "reasoning") {
      return [];
    }
    const content: ThreadMessageLike["content"][number][] = [];
    if (typeof message.content === "string" && message.content !== "") {
      content.push({
        type: message.role === "reasoning" ? "reasoning" : "text",
        text: message.content,
      });
    }
    const toolCalls = (message as Message & { toolCalls?: unknown[] }).toolCalls;
    if (Array.isArray(toolCalls)) {
      for (const raw of toolCalls) {
        if (!isRecord(raw)) continue;
        const fn = isRecord(raw.function) ? raw.function : {};
        const id = String(raw.id ?? raw.toolName ?? "");
        const argsText =
          typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(raw.args ?? {});
        let args: unknown = raw.args ?? {};
        try {
          args = JSON.parse(argsText);
        } catch {
          /* 保留流式参数文本。 */
        }
        content.push({
          type: "tool-call",
          toolCallId: id,
          toolName: String(raw.name ?? raw.toolName ?? fn.name ?? "tool"),
          args,
          argsText,
          ...(raw.result !== undefined
            ? { result: raw.result }
            : results.has(id)
              ? { result: results.get(id) }
              : {}),
          ...(raw.status === "error" || raw.isError === true ? { isError: true } : {}),
        } as ToolCallMessagePart);
      }
    }
    return [
      {
        id: String(message.id),
        role: message.role === "user" ? "user" : "assistant",
        content,
      } as ThreadMessageLike,
    ];
  });
}
