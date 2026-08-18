"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import { displayAgentName, type AgentSummary } from "@/lib/agents";

export type Conversation = {
  id: string;
  agent_id: string;
  title: string | null;
  latest_message_content: string | null;
  updated_at: string;
};

type ConversationListProps = {
  activeThreadId: string | null;
  agents: AgentSummary[];
  selectedAgentId: string | null;
  refreshKey: number;
  streamingThreadIds: ReadonlySet<string>;
  awaitingApprovalThreadIds?: ReadonlySet<string>;
  onNewConversation: () => void;
  onSelectAgent: (agentId: string) => void;
  onSelectThread: (conversation: Conversation) => void;
  onThreadDeleted: (threadId: string) => void;
};

type ConversationGroup = {
  label: string;
  conversations: Conversation[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isConversation(value: unknown): value is Conversation {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.agent_id === "string" &&
    (typeof value.title === "string" || value.title === null) &&
    (typeof value.latest_message_content === "string" || value.latest_message_content === null) &&
    typeof value.updated_at === "string"
  );
}

function parseConversations(value: unknown): Conversation[] | null {
  if (!isRecord(value) || !Array.isArray(value.threads)) {
    return null;
  }

  const conversations: Conversation[] = [];

  for (const thread of value.threads) {
    if (!isConversation(thread)) {
      return null;
    }

    conversations.push(thread);
  }

  return conversations;
}

function conversationLabel(conversation: Conversation): string {
  const source = conversation.title ?? conversation.latest_message_content ?? "新会话";
  return source.replace(/\s+/g, " ").trim().slice(0, 40) || "新会话";
}

function formatUpdatedAt(value: string): string {
  const date = new Date(value);

  if (!Number.isFinite(date.getTime())) {
    return "";
  }

  const now = new Date();
  const elapsedMinutes = Math.floor((now.getTime() - date.getTime()) / 60_000);

  if (elapsedMinutes < 1) {
    return "刚刚";
  }

  if (elapsedMinutes < 60) {
    return `${elapsedMinutes} 分钟前`;
  }

  if (elapsedMinutes < 24 * 60 && now.getDate() === date.getDate()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }

  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function groupConversations(conversations: Conversation[]): ConversationGroup[] {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const weekStart = todayStart - 6 * 24 * 60 * 60 * 1_000;

  const today: Conversation[] = [];
  const recent: Conversation[] = [];
  const earlier: Conversation[] = [];

  for (const conversation of conversations) {
    const updatedAt = new Date(conversation.updated_at).getTime();

    if (Number.isFinite(updatedAt) && updatedAt >= todayStart) {
      today.push(conversation);
    } else if (Number.isFinite(updatedAt) && updatedAt >= weekStart) {
      recent.push(conversation);
    } else {
      earlier.push(conversation);
    }
  }

  return [
    { label: "今天", conversations: today },
    { label: "最近 7 天", conversations: recent },
    { label: "更早", conversations: earlier },
  ].filter((group) => group.conversations.length > 0);
}

export function ConversationList({
  activeThreadId,
  agents,
  selectedAgentId,
  refreshKey,
  streamingThreadIds,
  awaitingApprovalThreadIds = new Set<string>(),
  onNewConversation,
  onSelectAgent,
  onSelectThread,
  onThreadDeleted,
}: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadedAgentId, setLoadedAgentId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [menuThreadId, setMenuThreadId] = useState<string | null>(null);
  const [renamingThreadId, setRenamingThreadId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [busyThreadId, setBusyThreadId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    if (selectedAgentId === null) {
      return () => {
        isCurrent = false;
        controller.abort();
      };
    }

    const agentId = selectedAgentId;

    void (async () => {
      try {
        const query = new URLSearchParams({ limit: "20", agent_id: agentId });
        const response = await fetch(`/api/threads?${query}`, {
          cache: "no-store",
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error("无法读取最近会话。");
        }

        const parsed = parseConversations((await response.json()) as unknown);

        if (parsed === null) {
          throw new Error("最近会话格式无效。");
        }

        if (isCurrent) {
          setConversations(parsed);
          setError(null);
          setLoadedAgentId(agentId);
          setIsLoading(false);
        }
      } catch (caughtError: unknown) {
        if (!isCurrent || controller.signal.aborted) {
          return;
        }

        setError(caughtError instanceof Error ? caughtError.message : "无法读取最近会话。");
        setLoadedAgentId(agentId);
      } finally {
        if (isCurrent) {
          setIsLoading(false);
        }
      }
    })();

    return () => {
      isCurrent = false;
      controller.abort();
    };
  }, [refreshKey, selectedAgentId]);

  const isLoadingCurrentAgent = isLoading || selectedAgentId !== loadedAgentId;
  const currentError = selectedAgentId === loadedAgentId ? error : null;

  const groups = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();

    const filtered = normalizedQuery
      ? conversations.filter((conversation) => {
          const searchableText = [
            conversationLabel(conversation),
            conversation.latest_message_content ?? "",
          ]
            .join(" ")
            .toLocaleLowerCase();

          return searchableText.includes(normalizedQuery);
        })
      : conversations;

    return groupConversations(filtered);
  }, [conversations, query]);

  function beginRename(conversation: Conversation) {
    setMenuThreadId(null);
    setRenamingThreadId(conversation.id);
    setRenameDraft(conversation.title ?? conversationLabel(conversation));
  }

  async function submitRename(event: FormEvent<HTMLFormElement>, threadId: string) {
    event.preventDefault();
    event.stopPropagation();

    const title = renameDraft.trim();
    setBusyThreadId(threadId);

    try {
      const response = await fetch(`/api/threads/${threadId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || null }),
      });

      if (!response.ok) {
        throw new Error("重命名失败，请稍后重试。");
      }

      const payload: unknown = await response.json();
      if (!isRecord(payload) || (payload.title !== null && typeof payload.title !== "string")) {
        throw new Error("重命名响应无效。");
      }

      setConversations((current) =>
        current.map((item) =>
          item.id === threadId
            ? {
                ...item,
                title: typeof payload.title === "string" ? payload.title : null,
                updated_at:
                  typeof payload.updated_at === "string" ? payload.updated_at : item.updated_at,
              }
            : item,
        ),
      );
      setRenamingThreadId(null);
      setError(null);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "重命名失败，请稍后重试。");
    } finally {
      setBusyThreadId(null);
    }
  }

  async function deleteThread(threadId: string) {
    setMenuThreadId(null);

    if (!window.confirm("删除后会话将从列表中隐藏，确定删除？")) {
      return;
    }

    setBusyThreadId(threadId);

    try {
      const response = await fetch(`/api/threads/${threadId}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("删除失败，请稍后重试。");
      }

      setConversations((current) => current.filter((item) => item.id !== threadId));
      setError(null);
      onThreadDeleted(threadId);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "删除失败，请稍后重试。");
    } finally {
      setBusyThreadId(null);
    }
  }

  return (
    <section className="agentos-conversation-list">
      <header className="agentos-conversation-list-header">
        <label className="agentos-agent-picker">
          <span>当前助手</span>
          <select
            aria-label="选择助手"
            value={selectedAgentId ?? ""}
            disabled={agents.length === 0}
            onChange={(event) => onSelectAgent(event.target.value)}
            className="agentos-agent-select disabled:cursor-wait disabled:opacity-60"
          >
            {agents.length === 0 ? <option value="">正在加载助手…</option> : null}
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {displayAgentName(agent.name)}
              </option>
            ))}
          </select>
        </label>
        <div className="agentos-conversation-toolbar">
          <p className="agentos-list-title">会话</p>
          <button
            type="button"
            onClick={onNewConversation}
            disabled={selectedAgentId === null}
            className="agentos-list-new-button disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span aria-hidden="true">＋</span>
            新建
          </button>
        </div>
        <input
          aria-label="搜索已加载会话"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索会话"
          className="agentos-search-input"
        />
      </header>

      {currentError ? (
        <p role="alert" className="agentos-list-feedback is-error">
          {currentError}
        </p>
      ) : isLoadingCurrentAgent ? (
        <p className="agentos-list-feedback">读取会话中…</p>
      ) : groups.length === 0 ? (
        <div className="agentos-list-feedback">
          <p>{query.trim() ? "没有匹配的已加载会话。" : "暂无会话。"}</p>
          {!query.trim() ? (
            <button
              type="button"
              onClick={onNewConversation}
              disabled={selectedAgentId === null}
              className="agentos-list-empty-action disabled:cursor-not-allowed disabled:opacity-50"
            >
              新建会话
            </button>
          ) : null}
        </div>
      ) : (
        <nav
          aria-label="最近会话"
          className="agentos-conversation-list-scroll min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          {groups.map((group) => (
            <section key={group.label} className="agentos-conversation-group">
              <p className="agentos-conversation-group-label">{group.label}</p>
              {group.conversations.map((conversation) => {
                const active = conversation.id === activeThreadId;
                const preview = conversation.latest_message_content ?? "暂无消息";
                const isBusy = busyThreadId === conversation.id;
                const isRenaming = renamingThreadId === conversation.id;
                const isStreaming = streamingThreadIds.has(conversation.id);
                const isAwaitingApproval = awaitingApprovalThreadIds.has(conversation.id);

                return (
                  <div
                    key={conversation.id}
                    className={`agentos-conversation-item ${active ? "is-active" : ""}`}
                  >
                    {isRenaming ? (
                      <form
                        className="agentos-rename-form"
                        onSubmit={(event) => void submitRename(event, conversation.id)}
                      >
                        <input
                          aria-label="会话标题"
                          value={renameDraft}
                          maxLength={80}
                          autoFocus
                          disabled={isBusy}
                          onChange={(event) => setRenameDraft(event.target.value)}
                          className="agentos-rename-input"
                        />
                        <button
                          type="submit"
                          disabled={isBusy}
                          className="agentos-rename-action is-save disabled:opacity-40"
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => setRenamingThreadId(null)}
                          className="agentos-rename-action disabled:opacity-40"
                        >
                          取消
                        </button>
                      </form>
                    ) : (
                      <div className="agentos-conversation-row">
                        <button
                          type="button"
                          onClick={() => onSelectThread(conversation)}
                          disabled={isBusy}
                          aria-current={active ? "page" : undefined}
                          className="agentos-conversation-button disabled:cursor-not-allowed"
                        >
                          <div className="agentos-conversation-title-row">
                            <p className="agentos-conversation-title">
                              {conversationLabel(conversation)}
                              {isStreaming ? (
                                <span className="agentos-conversation-status is-streaming">
                                  处理中
                                </span>
                              ) : null}
                              {!isStreaming && isAwaitingApproval ? (
                                <span className="agentos-conversation-status is-awaiting">
                                  等待确认
                                </span>
                              ) : null}
                            </p>
                            <time
                              className="agentos-conversation-time"
                              dateTime={conversation.updated_at}
                            >
                              {formatUpdatedAt(conversation.updated_at)}
                            </time>
                          </div>
                          <p className="agentos-conversation-preview">{preview}</p>
                        </button>

                        <button
                          type="button"
                          aria-label="会话操作"
                          disabled={isStreaming || isBusy}
                          onClick={() =>
                            setMenuThreadId((current) =>
                              current === conversation.id ? null : conversation.id,
                            )
                          }
                          className="agentos-conversation-menu-button disabled:opacity-40"
                        >
                          ⋯
                        </button>
                      </div>
                    )}

                    {menuThreadId === conversation.id ? (
                      <div className="agentos-conversation-menu">
                        <button
                          type="button"
                          className="agentos-conversation-menu-action"
                          onClick={() => beginRename(conversation)}
                        >
                          重命名
                        </button>
                        <button
                          type="button"
                          className="agentos-conversation-menu-action is-danger"
                          onClick={() => void deleteThread(conversation.id)}
                        >
                          删除
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </section>
          ))}
        </nav>
      )}
    </section>
  );
}
