"use client";

import { HttpAgent, type Message } from "@ag-ui/client";
import { FormEvent, Fragment, KeyboardEvent, useEffect, useRef, useState } from "react";

import {
  summarizeToolResultContent,
  ToolCallCard,
  type ToolCallState,
} from "@/components/chat/tool-call-card";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type ThreadHistory = {
  thread_id: string;
  messages: ChatMessage[];
  toolCalls: ToolCallState[];
};

type ReasoningState = {
  messageId: string;
  content: string;
  completed: boolean;
};

type ChatPanelProps = {
  selectedThreadId: string | null | undefined;
  onNewConversation: () => void;
  onRunStarted: (runId: string) => void;
  onStreamingChanged: (isStreaming: boolean) => void;
  onThreadChanged: (threadId: string | null) => void;
  onRunFinalized: () => void;
};

const STARTER_PROMPTS = [
  "帮我梳理这个需求的目标、约束和下一步。",
  "请给出一个可执行的实施方案，并说明主要风险。",
  "请审查下面的思路，指出不成立的假设。",
];

const AUTO_SCROLL_THRESHOLD = 96;
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

function toDisplayMessages(messages: readonly Message[]): ChatMessage[] {
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
      "toolCalls" in message &&
      Array.isArray(message.toolCalls) &&
      message.toolCalls.length > 0;

    // AG-UI may keep an assistant shell that only holds toolCalls; skip empty shells.
    if (message.role === "assistant" && !message.content && hasToolCalls) {
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

function upsertToolCall(
  current: ToolCallState[],
  next: ToolCallState,
): ToolCallState[] {
  const index = current.findIndex((item) => item.id === next.id);
  if (index === -1) {
    return [...current, next];
  }

  const updated = [...current];
  updated[index] = next;
  return updated;
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

function ReasoningPanel({
  reasoning,
  isExpanded,
  onToggle,
}: {
  reasoning: ReasoningState;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <section
      className={`agentos-reasoning max-w-[92%] sm:max-w-[85%] ${
        reasoning.completed ? "" : "agentos-reasoning-running"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isExpanded}
        className="agentos-reasoning-toggle"
      >
        <span className="agentos-reasoning-title">
          <span aria-hidden="true" className="agentos-reasoning-indicator" />
          {reasoning.completed ? "Thinking trace" : "Thinking..."}
        </span>
        <span className="agentos-reasoning-state">
          {reasoning.completed ? "已完成" : "运行中"} · {isExpanded ? "收起" : "展开"}
        </span>
      </button>

      {isExpanded && reasoning.content ? (
        <pre className="agentos-reasoning-content">{reasoning.content}</pre>
      ) : null}
    </section>
  );
}

export function ChatPanel({
  selectedThreadId,
  onNewConversation,
  onRunStarted,
  onStreamingChanged,
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
  const [reasoning, setReasoning] = useState<ReasoningState | null>(null);
  const [isReasoningExpanded, setIsReasoningExpanded] = useState(true);
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);

  const agentRef = useRef<HttpAgent | null>(null);
  const cancellationRequestedRef = useRef(false);
  const autoScrollRef = useRef(true);
  const initialThreadIdRef = useRef<string | null | undefined>(undefined);
  const messagesViewportRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  if (agentRef.current === null) {
    agentRef.current = createAgent("new", []);
  }

  useEffect(() => {
    onStreamingChanged(isStreaming);
  }, [isStreaming, onStreamingChanged]);

  useEffect(() => {
    if (autoScrollRef.current) {
      scrollMessagesToBottom();
    }
  }, [messages, reasoning?.content, toolCalls]);

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
    const controller = new AbortController();
    let isCurrent = true;

    const initialThreadId =
      initialThreadIdRef.current === undefined
        ? new URL(window.location.href).searchParams.get("thread")
        : initialThreadIdRef.current;

    initialThreadIdRef.current = initialThreadId;

    const requestedThreadId = selectedThreadId === undefined ? initialThreadId : selectedThreadId;

    if (requestedThreadId === null || requestedThreadId === threadId) {
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
          setReasoning(null);
          setToolCalls(history.toolCalls);
          setError(null);
          updateThreadInUrl(history.thread_id);
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
  }, [onThreadChanged, selectedThreadId, threadId]);

  function scrollMessagesToBottom(behavior: ScrollBehavior = "auto") {
    const viewport = messagesViewportRef.current;

    if (viewport === null) {
      return;
    }

    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior,
    });
  }

  function stopStreaming() {
    cancellationRequestedRef.current = true;
    agentRef.current?.abortRun();
  }

  function scrollToLatest() {
    autoScrollRef.current = true;
    setShowScrollToLatest(false);
    scrollMessagesToBottom("smooth");
  }

  function handleMessageScroll() {
    const viewport = messagesViewportRef.current;

    if (viewport === null) {
      return;
    }

    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    autoScrollRef.current = distanceFromBottom < AUTO_SCROLL_THRESHOLD;
    setShowScrollToLatest(!autoScrollRef.current);
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
    agent.addMessage({
      id: userMessageId,
      role: "user",
      content,
    });

    setMessages(toDisplayMessages(agent.messages));
    setDraft("");
    setError(null);
    setReasoning(null);
    setIsReasoningExpanded(true);
    setIsStreaming(true);
    autoScrollRef.current = true;
    setShowScrollToLatest(false);
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
        onReasoningStartEvent: ({ event }) => {
          setReasoning({
            messageId: event.messageId,
            content: "",
            completed: false,
          });
          setIsReasoningExpanded(true);
        },
        onReasoningMessageContentEvent: ({ event, reasoningMessageBuffer }) => {
          setReasoning({
            messageId: event.messageId,
            content: reasoningMessageBuffer,
            completed: false,
          });
        },
        onReasoningEndEvent: ({ event }) => {
          setReasoning((current) =>
            current?.messageId === event.messageId ? { ...current, completed: true } : current,
          );
        },
        onToolCallStartEvent: ({ event }) => {
          setToolCalls((current) =>
            upsertToolCall(current, {
              id: event.toolCallId,
              toolName: event.toolCallName,
              argsText: "",
              status: "running",
              expanded: true,
              afterMessageId: userMessageId,
            }),
          );
        },
        onToolCallArgsEvent: ({ event, toolCallBuffer, toolCallName }) => {
          setToolCalls((current) => {
            const existing = current.find((item) => item.id === event.toolCallId);
            return upsertToolCall(current, {
              id: event.toolCallId,
              toolName: toolCallName || existing?.toolName || "tool",
              argsText: toolCallBuffer,
              status: existing?.status ?? "running",
              resultSummary: existing?.resultSummary,
              provider: existing?.provider,
              expanded: existing?.expanded ?? true,
              afterMessageId: existing?.afterMessageId ?? userMessageId,
            });
          });
        },
        onToolCallEndEvent: ({ event, toolCallName, toolCallArgs }) => {
          setToolCalls((current) => {
            const existing = current.find((item) => item.id === event.toolCallId);
            return upsertToolCall(current, {
              id: event.toolCallId,
              toolName: toolCallName || existing?.toolName || "tool",
              argsText: existing?.argsText || JSON.stringify(toolCallArgs ?? {}),
              status: existing?.status ?? "running",
              resultSummary: existing?.resultSummary,
              provider: existing?.provider,
              expanded: existing?.expanded ?? true,
              afterMessageId: existing?.afterMessageId ?? userMessageId,
            });
          });
        },
        onToolCallResultEvent: ({ event }) => {
          const summarized = summarizeToolResultContent(event.content);
          setToolCalls((current) => {
            const existing = current.find((item) => item.id === event.toolCallId);
            return upsertToolCall(current, {
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
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void sendMessage();
    }
  }

  function startNewConversation() {
    if (!isStreaming && !isLoadingHistory) {
      onNewConversation();
    }
  }

  const latestUserMessageIndex = messages.reduce(
    (latestIndex, message, index) => (message.role === "user" ? index : latestIndex),
    -1,
  );

  const currentAssistantMessageIndex = messages.findIndex(
    (message, index) => index > latestUserMessageIndex && message.role === "assistant",
  );

  function toggleToolCall(toolCallId: string) {
    setToolCalls((current) =>
      current.map((item) =>
        item.id === toolCallId ? { ...item, expanded: !item.expanded } : item,
      ),
    );
  }

  const statusLabel = isLoadingHistory
    ? "读取会话中"
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
          <p className="text-sm font-semibold text-zinc-950">
            {threadId === null ? "新建 Agent 会话" : "Agent conversation"}
          </p>
          <p className="mt-1 text-xs text-zinc-500">
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
            disabled={isStreaming || isLoadingHistory}
            className="agentos-new-chat-button text-xs font-medium disabled:cursor-not-allowed disabled:opacity-40"
          >
            新建对话
          </button>
        </div>
      </header>

      <div
        ref={messagesViewportRef}
        onScroll={handleMessageScroll}
        className="agentos-message-viewport relative min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 sm:p-5"
      >
        <div className="mx-auto max-w-3xl space-y-5">
          {messages.length === 0 ? (
            <div className="agentos-empty-state flex min-h-72 flex-col justify-center py-8">
              <p className="text-lg font-semibold text-zinc-950">从一个任务开始</p>
              <p className="mt-2 max-w-lg text-sm leading-6 text-zinc-500">
                AgentOS 会在同一条运行轨迹中展示对话、Thinking 与最终执行结果。
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
            messages.map((message, index) => (
              <Fragment key={message.id}>
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
                    <p className="break-words whitespace-pre-wrap">{message.content}</p>
                  ) : message.role === "assistant" && isStreaming ? (
                    <p aria-live="polite" className="text-zinc-500">
                      正在生成最终回答...
                    </p>
                  ) : null}
                </article>

                {message.role === "user" ? (
                  <>
                    {reasoning && index === latestUserMessageIndex ? (
                      <ReasoningPanel
                        reasoning={reasoning}
                        isExpanded={isReasoningExpanded}
                        onToggle={() => setIsReasoningExpanded((current) => !current)}
                      />
                    ) : null}
                    {toolCalls
                      .filter((toolCall) => toolCall.afterMessageId === message.id)
                      .map((toolCall) => (
                        <ToolCallCard
                          key={toolCall.id}
                          toolCall={toolCall}
                          onToggle={() => toggleToolCall(toolCall.id)}
                        />
                      ))}
                  </>
                ) : null}
              </Fragment>
            ))
          )}

          <div ref={messagesEndRef} />
        </div>

        {showScrollToLatest ? (
          <button
            type="button"
            onClick={scrollToLatest}
            className="agentos-scroll-latest absolute right-4 bottom-4 px-3 py-2 text-xs font-medium"
          >
            回到最新消息
          </button>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="agentos-chat-error border-t px-5 py-3 text-sm">
          {error}
        </p>
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
          disabled={isStreaming || isLoadingHistory || historyLoadFailed}
          maxLength={4_000}
          placeholder="输入任务、问题或需要 Agent 执行的操作"
          rows={1}
          className="agentos-composer-input block max-h-50 w-full resize-none overflow-y-hidden px-3 py-2 text-sm leading-6 outline-none disabled:cursor-not-allowed"
        />

        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-zinc-500">
            {draft.length}/4000 <span className="hidden sm:inline">Shift + Enter 换行</span>
          </span>

          <button
            type="submit"
            disabled={isLoadingHistory || historyLoadFailed || (!isStreaming && !draft.trim())}
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
