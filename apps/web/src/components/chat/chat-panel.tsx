"use client";

import { HttpAgent, type Message } from "@ag-ui/client";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type ThreadHistory = {
  thread_id: string;
  messages: ChatMessage[];
};

type ChatPanelProps = {
  onRunStarted: (runId: string) => void;
  onThreadChanged: (threadId: string | null) => void;
  onRunFinalized: () => void;
};

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
    });
  }

  return { thread_id: value.thread_id, messages };
}

function toAgentMessages(messages: ChatMessage[]): Message[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
  }));
}

function toDisplayMessages(messages: readonly Message[]): ChatMessage[] {
  const displayMessages: ChatMessage[] = [];

  for (const message of messages) {
    if (
      (message.role !== "user" && message.role !== "assistant") ||
      typeof message.content !== "string"
    ) {
      continue;
    }

    displayMessages.push({
      id: message.id,
      role: message.role,
      content: message.content,
    });
  }

  return displayMessages;
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

export function ChatPanel({ onRunStarted, onThreadChanged, onRunFinalized }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyLoadFailed, setHistoryLoadFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const agentRef = useRef<HttpAgent | null>(null);
  const cancellationRequestedRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  if (agentRef.current === null) {
    agentRef.current = createAgent("new", []);
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    return () => {
      // Cancel the browser request when the chat panel unmounts.
      agentRef.current?.abortRun();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    void (async () => {
      const requestedThreadId = new URL(window.location.href).searchParams.get("thread");

      if (requestedThreadId === null) {
        if (isCurrent) {
          setIsLoadingHistory(false);
        }
        return;
      }

      if (!isUuid(requestedThreadId)) {
        if (isCurrent) {
          setHistoryLoadFailed(true);
          setError("会话链接无效。请新建对话后再继续。");
          setIsLoadingHistory(false);
        }
        return;
      }

      try {
        const response = await fetch(`/api/threads/${requestedThreadId}/messages`, {
          cache: "no-store",
          signal: controller.signal,
        });

        if (response.status === 404) {
          throw new Error("找不到该会话。请新建对话后再继续。");
        }

        if (!response.ok) {
          throw new Error("无法读取会话历史，请稍后重试。");
        }

        const history = parseThreadHistory((await response.json()) as unknown);

        if (history === null || history.thread_id !== requestedThreadId) {
          throw new Error("会话历史格式无效。");
        }

        if (isCurrent) {
          agentRef.current = createAgent(history.thread_id, history.messages);
          setMessages(history.messages);
          setThreadId(history.thread_id);
          onThreadChanged(history.thread_id);
          setError(null);
        }
      } catch (caughtError: unknown) {
        if (!isCurrent || controller.signal.aborted) {
          return;
        }

        setHistoryLoadFailed(true);
        setError(caughtError instanceof Error ? caughtError.message : "无法读取会话历史。");
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
  }, [onThreadChanged]);

  function stopStreaming() {
    cancellationRequestedRef.current = true;
    agentRef.current?.abortRun();
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

    agent.addMessage({
      id: crypto.randomUUID(),
      role: "user",
      content,
    });

    setMessages(toDisplayMessages(agent.messages));
    setDraft("");
    setError(null);
    setIsStreaming(true);
    cancellationRequestedRef.current = false;

    try {
      await agent.runAgent(undefined, {
        onMessagesChanged: ({ messages: nextMessages }) => {
          setMessages(toDisplayMessages(nextMessages));
        },
        onRunStartedEvent: ({ event, agent: runningAgent }) => {
          if (isUuid(event.runId)) {
            onRunStarted(event.runId);
          }

          // The backend owns durable IDs; switch subsequent browser requests to its Thread.
          if (isUuid(event.threadId)) {
            runningAgent.threadId = event.threadId;
            setThreadId(event.threadId);
            onThreadChanged(event.threadId);
            updateThreadInUrl(event.threadId);
          }
        },
        onRunErrorEvent: ({ event }) => {
          setError(event.message || "Agent 生成失败，请稍后重试。");
        },
      });
    } catch (caughtError: unknown) {
      if (!cancellationRequestedRef.current) {
        setError(agentErrorMessage(caughtError));
      }
    } finally {
      cancellationRequestedRef.current = false;
      setIsStreaming(false);
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
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  function startNewConversation() {
    if (isStreaming || isLoadingHistory) {
      return;
    }

    onThreadChanged(null);
    agentRef.current = createAgent("new", []);

    const url = new URL(window.location.href);
    url.searchParams.delete("thread");
    window.history.replaceState(window.history.state, "", url);

    setMessages([]);
    setThreadId(null);
    setError(null);
    setHistoryLoadFailed(false);
  }

  return (
    <section className="flex min-h-[36rem] flex-col border border-zinc-200 bg-white">
      <header className="flex items-center justify-between border-b border-zinc-200 px-5 py-4">
        <div>
          <p className="text-sm font-semibold text-zinc-950">对话</p>
          <p className="mt-1 text-xs text-zinc-500">{threadId === null ? "新会话" : "当前会话"}</p>
        </div>
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={startNewConversation}
            disabled={isStreaming || isLoadingHistory}
            className="text-sm text-zinc-600 transition hover:text-zinc-950 disabled:cursor-not-allowed disabled:text-zinc-300"
          >
            新建对话
          </button>
          <p aria-live="polite" className="text-xs text-zinc-500">
            {isLoadingHistory ? "读取中" : isStreaming ? "生成中" : "就绪"}
          </p>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {messages.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无消息</p>
        ) : (
          messages.map((message) => (
            <article
              key={message.id}
              className={`max-w-[85%] border px-4 py-3 text-sm leading-6 ${
                message.role === "user"
                  ? "ml-auto border-zinc-900 bg-zinc-900 text-white"
                  : "border-zinc-200 bg-zinc-50 text-zinc-800"
              }`}
            >
              <p className="mb-1 text-xs opacity-70">
                {message.role === "user" ? "你" : "AgentOS"}
              </p>
              <p className="break-words whitespace-pre-wrap">{message.content}</p>
            </article>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {error ? (
        <p
          role="alert"
          className="border-t border-rose-200 bg-rose-50 px-5 py-3 text-sm text-rose-700"
        >
          {error}
        </p>
      ) : null}

      <form onSubmit={handleSubmit} className="border-t border-zinc-200 p-4">
        <textarea
          aria-label="输入消息"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming || isLoadingHistory || historyLoadFailed}
          maxLength={4_000}
          placeholder="输入消息"
          rows={3}
          className="block w-full resize-none border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-950 outline-none placeholder:text-zinc-400 focus:border-zinc-700 disabled:bg-zinc-100"
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-zinc-500">{draft.length}/4000</span>
          <button
            type="submit"
            disabled={isLoadingHistory || historyLoadFailed || (!isStreaming && !draft.trim())}
            className="border border-zinc-900 bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:border-zinc-300 disabled:bg-zinc-300"
          >
            {isStreaming ? "停止" : "发送"}
          </button>
        </div>
      </form>
    </section>
  );
}
