"use client";

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

type SseFrame = {
  event: string;
  data: string;
};

function parseSseFrame(frame: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    }

    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).replace(/^ /, ""));
    }
  }

  return dataLines.length > 0 ? { event, data: dataLines.join("\n") } : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parsePayload(data: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(data);
    return isRecord(value) ? value : null;
  } catch {
    return null;
  }
}

function payloadText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === "string" ? value : null;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
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

export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [historyLoadFailed, setHistoryLoadFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const messageCountRef = useRef(0);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  useEffect(() => {
    return () => {
      // Leaving the page must cancel the browser request and the upstream model stream.
      abortControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    void (async () => {
      // Yield once so restoring external URL state does not synchronously cascade from mount.
      await Promise.resolve();

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
          setMessages(history.messages);
          setThreadId(history.thread_id);
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
  }, []);

  function createMessageId(role: ChatMessage["role"]): string {
    messageCountRef.current += 1;
    return `${role}-${messageCountRef.current}`;
  }

  function stopStreaming() {
    abortControllerRef.current?.abort();
  }

  async function sendMessage() {
    const message = draft.trim();

    if (!message || isStreaming || isLoadingHistory || historyLoadFailed) {
      return;
    }

    const controller = new AbortController();
    const userMessage: ChatMessage = {
      id: createMessageId("user"),
      role: "user",
      content: message,
    };
    const assistantMessageId = createMessageId("assistant");

    abortControllerRef.current = controller;
    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantMessageId, role: "assistant", content: "" },
    ]);
    setDraft("");
    setError(null);
    setIsStreaming(true);

    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          ...(threadId === null ? {} : { threadId }),
        }),
        signal: controller.signal,
      });

      if (!response.ok || response.body === null) {
        throw new Error("无法连接 Agent 服务。");
      }

      const responseThreadId = response.headers.get("x-agentos-thread-id");
      if (responseThreadId === null || !isUuid(responseThreadId)) {
        throw new Error("Agent 未返回会话标识。");
      }

      // Keep the server-issued identity in the URL so a refresh restores this exact Thread.
      setThreadId(responseThreadId);
      const url = new URL(window.location.href);
      url.searchParams.set("thread", responseThreadId);
      window.history.replaceState(window.history.state, "", url);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let receivedDone = false;

      const processBuffer = () => {
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const rawFrame of frames) {
          const frame = parseSseFrame(rawFrame);
          if (frame === null) {
            continue;
          }

          const payload = parsePayload(frame.data);
          if (payload === null) {
            throw new Error("Agent 返回了无法识别的流式数据。");
          }

          if (frame.event === "text_delta") {
            const delta = payloadText(payload, "delta");
            if (delta === null) {
              throw new Error("Agent 返回了无效的文本事件。");
            }

            setMessages((current) =>
              current.map((item) =>
                item.id === assistantMessageId
                  ? { ...item, content: `${item.content}${delta}` }
                  : item,
              ),
            );
          }

          if (frame.event === "error") {
            throw new Error(payloadText(payload, "message") ?? "Agent 生成失败。");
          }

          if (frame.event === "done") {
            receivedDone = true;
          }
        }
      };

      try {
        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          processBuffer();
        }

        buffer += decoder.decode();
        processBuffer();
      } finally {
        reader.releaseLock();
      }

      if (!receivedDone && !controller.signal.aborted) {
        throw new Error("Agent 流在完成前中断。");
      }
    } catch (caughtError: unknown) {
      if (!controller.signal.aborted) {
        setMessages((current) =>
          current.filter((item) => item.id !== assistantMessageId || item.content.length > 0),
        );
        setError(caughtError instanceof Error ? caughtError.message : "Agent 生成失败。");
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
        setIsStreaming(false);
      }
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
              <p className="break-words whitespace-pre-wrap">
                {message.content || (isStreaming ? "..." : "")}
              </p>
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
