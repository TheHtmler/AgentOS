"use client";

import { HttpAgent, type Message } from "@ag-ui/client";
import {
  ExportedMessageRepository,
  type AttachmentAdapter,
  type CompleteAttachment,
  type DictationAdapter,
  type PendingAttachment,
  type ThreadHistoryAdapter,
} from "@assistant-ui/react";
import { fromAgUiMessages, useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  historyToDisplayMessages,
  parseThreadHistory,
  readThreadHistoryResponse,
  type HistoryAttachment,
} from "@/lib/agui-runtime";
import { AgentOsAgUiTransport } from "@/lib/agentos-ag-ui-transport";

const UPLOAD_ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp";

type RuntimeOptions = {
  selectedThreadId: string | null | undefined;
  agentId: string | null;
  executionMode?: "normal" | "plan" | "execute";
  onThreadChanged: (threadId: string | null, agentId?: string) => void;
  onRunStarted?: (runId: string) => void;
  onError?: (message: string) => void;
  dictationAdapter?: DictationAdapter;
};

type RunState = {
  status?: string;
  pending_interrupts?: unknown[];
};

type RecoverableRepository = ExportedMessageRepository & { unstable_resume?: boolean };

const ACTIVE_RUN_STATUSES = new Set(["queued", "running"]);
const RECOVERY_DELAYS_MS = [1_000, 2_000, 5_000] as const;
const RECOVERY_MAX_ATTEMPTS = 60;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function historyAttachmentPart(attachment: HistoryAttachment) {
  return {
    type: "binary",
    url: `/api/uploads/${attachment.id}/content`,
    mimeType: attachment.mime_type || "application/octet-stream",
    filename: attachment.title,
  };
}

function withOfficialHistoryAttachments(messages: readonly Message[]): unknown[] {
  return messages.map((message) => {
    const attachments = (message as Message & { uploadAttachments?: HistoryAttachment[] })
      .uploadAttachments;
    if (message.role !== "user" || attachments === undefined || attachments.length === 0) {
      return message;
    }
    return {
      ...message,
      content: [
        ...(typeof message.content === "string" && message.content !== ""
          ? [{ type: "text", text: message.content }]
          : []),
        ...attachments.map(historyAttachmentPart),
      ],
    };
  });
}

function toOfficialInterrupts(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (
      !isRecord(entry) ||
      typeof entry.id !== "string" ||
      typeof entry.tool_call_id !== "string" ||
      typeof entry.tool_name !== "string" ||
      !isRecord(entry.tool_args)
    ) {
      return [];
    }
    return [
      {
        id: entry.id,
        reason: "tool_call",
        message: "需要你的确认才能继续",
        toolCallId: entry.tool_call_id,
        responseSchema: {
          type: "object",
          properties: {
            approved: { type: "boolean" },
            reason: { type: "string" },
          },
          required: ["approved"],
        },
        expiresAt: typeof entry.expires_at === "string" ? entry.expires_at : undefined,
        metadata: {
          toolName: entry.tool_name,
          toolArgs: entry.tool_args,
        },
      },
    ];
  });
}

function attachInterruptMetadata(messages: unknown[], interrupts: Record<string, unknown>[]) {
  if (interrupts.length === 0) return messages;
  const next = [...messages];
  for (let index = next.length - 1; index >= 0; index -= 1) {
    const message = next[index];
    if (!isRecord(message) || message.role !== "assistant") continue;
    next[index] = {
      ...message,
      metadata: { custom: { agui: { interrupts } } },
    };
    return next;
  }
  next.push({
    id: `pending-interrupts-${interrupts[0]?.id ?? "run"}`,
    role: "assistant",
    content: "",
    metadata: { custom: { agui: { interrupts } } },
  });
  return next;
}

function abortableDelay(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException("操作已取消", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(signal.reason ?? new DOMException("操作已取消", "AbortError"));
    };
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function waitForPersistedRun(runId: string, signal: AbortSignal): Promise<RunState> {
  let lastError: unknown;
  for (let attempt = 0; attempt < RECOVERY_MAX_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(`/api/runs/${runId}`, { cache: "no-store", signal });
      if (!response.ok) throw new Error(`无法恢复运行状态（${response.status}）`);
      const state = (await response.json()) as RunState;
      if (typeof state.status !== "string" || !ACTIVE_RUN_STATUSES.has(state.status)) return state;
    } catch (error) {
      if (signal.aborted) throw error;
      lastError = error;
    }
    await abortableDelay(RECOVERY_DELAYS_MS[Math.min(attempt, 2)], signal);
  }
  throw lastError instanceof Error
    ? lastError
    : new Error("运行恢复等待超时，请稍后刷新会话查看结果。");
}

export function useAgentOsAssistantRuntime({
  selectedThreadId,
  agentId,
  executionMode = "normal",
  onThreadChanged,
  onRunStarted,
  onError,
  dictationAdapter,
}: RuntimeOptions) {
  const [historyVersion, setHistoryVersion] = useState(0);

  const [transport] = useState(
    () =>
      new AgentOsAgUiTransport({
        onServerIds: (threadId, runId) => {
          onRunStarted?.(runId);
          onThreadChanged(threadId);
        },
      }),
  );

  useEffect(() => {
    transport.setCallbacks({
      onServerIds: (threadId, runId) => {
        onRunStarted?.(runId);
        onThreadChanged(threadId);
      },
    });
  }, [onRunStarted, onThreadChanged, transport]);

  const agent = useMemo(() => {
    return new HttpAgent({
      url: "/api/ag-ui/runs",
      threadId: selectedThreadId ?? "new",
      fetch: transport.fetch,
      headers: {
        ...(agentId === null ? {} : { "X-AgentOS-Agent-Id": agentId }),
        "X-AgentOS-Run-Mode": executionMode,
      },
    });
  }, [agentId, executionMode, selectedThreadId, transport]);

  const ensureThreadForUpload = useCallback(async (): Promise<string> => {
    if (transport.serverThreadId !== null) return transport.serverThreadId;
    if (selectedThreadId) return selectedThreadId;

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
    const threadAgentId = typeof payload.agent_id === "string" ? payload.agent_id : agentId;
    transport.restoreServerRun(null, payload.id);
    onThreadChanged(payload.id, threadAgentId ?? undefined);
    return payload.id;
  }, [agentId, onThreadChanged, selectedThreadId, transport]);

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
        return {
          ...attachment,
          status: { type: "complete" },
          content: [{ type: "text", text: `artifact_id=${payload.artifact_id}` }],
        };
      },
      async remove() {},
    }),
    [ensureThreadForUpload],
  );

  const loadHistoryRepository = useCallback(
    async (
      options: { signal?: AbortSignal; expectedTransportRevision?: number } = {},
    ): Promise<RecoverableRepository> => {
      const { signal, expectedTransportRevision } = options;
      if (!selectedThreadId) return ExportedMessageRepository.fromArray([]);
      const response = await fetch(`/api/threads/${selectedThreadId}/messages`, {
        cache: "no-store",
        signal,
      });
      const historyPayload = await readThreadHistoryResponse(response);
      if (historyPayload === null) {
        const reset = transport.restoreServerRun(null, null, expectedTransportRevision);
        if (reset) onThreadChanged(null);
        return ExportedMessageRepository.fromArray([]);
      }
      const history = parseThreadHistory(historyPayload);
      if (history === null || history.thread_id !== selectedThreadId) {
        throw new Error("会话历史格式无效");
      }

      let wireMessages = withOfficialHistoryAttachments(historyToDisplayMessages(history));
      if (history.latest_run !== null) {
        const restored = transport.restoreServerRun(
          history.latest_run.id,
          history.thread_id,
          expectedTransportRevision,
        );
        if (restored) onRunStarted?.(history.latest_run.id);
        if (history.latest_run.status === "waiting_approval") {
          const runResponse = await fetch(`/api/runs/${history.latest_run.id}`, {
            cache: "no-store",
            signal,
          });
          const runState: RunState | null = runResponse.ok ? await runResponse.json() : null;
          wireMessages = attachInterruptMetadata(
            wireMessages,
            toOfficialInterrupts(runState?.pending_interrupts),
          );
        }
      }
      onThreadChanged(history.thread_id, history.agent_id);
      const repository = ExportedMessageRepository.fromArray(fromAgUiMessages(wireMessages));
      return Object.assign(repository, {
        unstable_resume:
          history.latest_run !== null && ACTIVE_RUN_STATUSES.has(history.latest_run.status),
      });
    },
    [onRunStarted, onThreadChanged, selectedThreadId, transport],
  );

  const historyAdapter = useMemo<ThreadHistoryAdapter>(
    () => ({
      load: () => loadHistoryRepository(),
      async *resume(options) {
        const runId = transport.serverRunId;
        const expectedTransportRevision = transport.revision;
        if (runId === null) throw new Error("缺少待恢复的服务端 Run 标识。");
        await waitForPersistedRun(runId, options.abortSignal);
        const repository = await loadHistoryRepository({
          signal: options.abortSignal,
          expectedTransportRevision,
        });
        const lastAssistant = [...repository.messages]
          .reverse()
          .find(({ message }) => message.role === "assistant")?.message;
        if (lastAssistant?.role === "assistant") {
          yield { content: lastAssistant.content, status: lastAssistant.status };
        }
      },
      async append() {
        // 消息由 AgentOS 服务端在 Run 事务中持久化，浏览器不重复写入。
      },
    }),
    [loadHistoryRepository, transport],
  );

  const runtime = useAgUiRuntime({
    agent,
    resumeTranscript: "appended",
    autoCancelPendingToolCalls: true,
    adapters: {
      attachments: attachmentAdapter,
      ...(dictationAdapter ? { dictation: dictationAdapter } : {}),
      history: historyAdapter,
    },
    onCancel: () => {
      const runId = transport.serverRunId;
      if (runId !== null) {
        void fetch(`/api/runs/${runId}/cancel`, { method: "POST", keepalive: true });
      }
    },
    onError: (error) => onError?.(error.message),
  });

  const refreshHistory = useCallback(async () => {
    const expectedTransportRevision = transport.revision;
    try {
      const repository = await loadHistoryRepository({ expectedTransportRevision });
      if (transport.revision !== expectedTransportRevision) return;
      runtime.thread.import(repository);
      setHistoryVersion((value) => value + 1);
      if (repository.unstable_resume && historyAdapter.resume) {
        runtime.thread.resumeRun({
          parentId: repository.headId ?? null,
          stream: historyAdapter.resume.bind(historyAdapter),
        });
      }
    } catch (error) {
      onError?.(error instanceof Error ? error.message : "会话历史刷新失败。");
    }
  }, [historyAdapter, loadHistoryRepository, onError, runtime, transport]);

  const interruptPayloads = useMemo(
    () => ({
      setInterruptPayload: (interruptId: string, payload: Record<string, unknown> | null) =>
        transport.setInterruptPayload(interruptId, payload),
    }),
    [transport],
  );

  return {
    runtime,
    historyVersion,
    refreshHistory,
    interruptPayloads,
  };
}
