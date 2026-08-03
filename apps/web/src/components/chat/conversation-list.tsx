"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Conversation = {
  id: string;
  title: string | null;
  latest_message_content: string | null;
  updated_at: string;
};

type ConversationListProps = {
  activeThreadId: string | null;
  refreshKey: number;
  streamingThreadIds: ReadonlySet<string>;
  onNewConversation: () => void;
  onSelectThread: (threadId: string) => void;
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
  refreshKey,
  streamingThreadIds,
  onNewConversation,
  onSelectThread,
  onThreadDeleted,
}: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [menuThreadId, setMenuThreadId] = useState<string | null>(null);
  const [renamingThreadId, setRenamingThreadId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [busyThreadId, setBusyThreadId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    void (async () => {
      try {
        const response = await fetch("/api/threads?limit=20", {
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
        }
      } catch (caughtError: unknown) {
        if (!isCurrent || controller.signal.aborted) {
          return;
        }

        setError(caughtError instanceof Error ? caughtError.message : "无法读取最近会话。");
      }
    })();

    return () => {
      isCurrent = false;
      controller.abort();
    };
  }, [refreshKey]);

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
    <section className="agentos-conversation-list flex h-full min-h-0 min-w-0 flex-col overflow-hidden border border-zinc-200 bg-white">
      <header className="border-b border-zinc-200 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-zinc-950">会话</p>
          <button
            type="button"
            onClick={onNewConversation}
            className="border border-zinc-300 px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:border-zinc-500 hover:text-zinc-950"
          >
            新建
          </button>
        </div>
        <input
          aria-label="搜索已加载会话"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索会话"
          className="mt-3 block w-full border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-500 focus:bg-white"
        />
      </header>

      {error ? (
        <p role="alert" className="px-4 py-4 text-sm text-rose-700">
          {error}
        </p>
      ) : groups.length === 0 ? (
        <p className="px-4 py-5 text-sm text-zinc-500">
          {query.trim() ? "没有匹配的已加载会话。" : "暂无已保存的会话。"}
        </p>
      ) : (
        <nav
          aria-label="最近会话"
          className="agentos-conversation-list-scroll min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          {groups.map((group) => (
            <section key={group.label} className="border-b border-zinc-100 last:border-b-0">
              <p className="px-3 pt-3 pb-1 text-xs font-medium tracking-wide text-zinc-400">
                {group.label}
              </p>
              {group.conversations.map((conversation) => {
                const active = conversation.id === activeThreadId;
                const preview = conversation.latest_message_content ?? "暂无消息";
                const isBusy = busyThreadId === conversation.id;
                const isRenaming = renamingThreadId === conversation.id;
                const isStreaming = streamingThreadIds.has(conversation.id);

                return (
                  <div
                    key={conversation.id}
                    className={`relative px-2 py-1 ${active ? "bg-zinc-900 text-white" : "text-zinc-900"}`}
                  >
                    {isRenaming ? (
                      <form
                        className="flex items-center gap-2 px-1 py-1.5"
                        onSubmit={(event) => void submitRename(event, conversation.id)}
                      >
                        <input
                          aria-label="会话标题"
                          value={renameDraft}
                          maxLength={80}
                          autoFocus
                          disabled={isBusy}
                          onChange={(event) => setRenameDraft(event.target.value)}
                          className="min-w-0 flex-1 border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 outline-none"
                        />
                        <button
                          type="submit"
                          disabled={isBusy}
                          className="shrink-0 text-xs font-medium text-teal-700 disabled:opacity-40"
                        >
                          保存
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => setRenamingThreadId(null)}
                          className="shrink-0 text-xs text-zinc-500"
                        >
                          取消
                        </button>
                      </form>
                    ) : (
                      <div className="flex min-w-0 items-stretch gap-1">
                        <button
                          type="button"
                          onClick={() => onSelectThread(conversation.id)}
                          disabled={isBusy}
                          aria-current={active ? "page" : undefined}
                          className={`min-w-0 flex-1 px-1 py-2 text-left transition disabled:cursor-not-allowed ${
                            active ? "" : "hover:bg-zinc-50"
                          }`}
                        >
                          <div className="flex min-w-0 items-start justify-between gap-3">
                            <p className="min-w-0 truncate text-sm font-medium">
                              {conversationLabel(conversation)}
                              {isStreaming ? (
                                <span
                                  className={`ml-2 text-[10px] font-semibold tracking-wide ${
                                    active ? "text-teal-200" : "text-teal-700"
                                  }`}
                                >
                                  生成中
                                </span>
                              ) : null}
                            </p>
                            <time
                              className={`shrink-0 text-xs ${active ? "text-zinc-300" : "text-zinc-400"}`}
                              dateTime={conversation.updated_at}
                            >
                              {formatUpdatedAt(conversation.updated_at)}
                            </time>
                          </div>
                          <p
                            className={`mt-1 truncate text-xs ${active ? "text-zinc-300" : "text-zinc-500"}`}
                          >
                            {preview}
                          </p>
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
                          className={`shrink-0 px-2 text-lg leading-none disabled:opacity-40 ${
                            active ? "text-zinc-200" : "text-zinc-400 hover:text-zinc-700"
                          }`}
                        >
                          ⋯
                        </button>
                      </div>
                    )}

                    {menuThreadId === conversation.id ? (
                      <div className="absolute top-10 right-2 z-20 min-w-28 border border-zinc-200 bg-white py-1 text-zinc-900 shadow-lg">
                        <button
                          type="button"
                          className="block w-full px-3 py-2 text-left text-xs hover:bg-zinc-50"
                          onClick={() => beginRename(conversation)}
                        >
                          重命名
                        </button>
                        <button
                          type="button"
                          className="block w-full px-3 py-2 text-left text-xs text-rose-700 hover:bg-rose-50"
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
