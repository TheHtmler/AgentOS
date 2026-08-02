"use client";

import { useEffect, useMemo, useState } from "react";

type Conversation = {
  id: string;
  title: string | null;
  latest_message_content: string | null;
  updated_at: string;
};

type ConversationListProps = {
  activeThreadId: string | null;
  refreshKey: number;
  isChatStreaming: boolean;
  onNewConversation: () => void;
  onSelectThread: (threadId: string) => void;
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
  isChatStreaming,
  onNewConversation,
  onSelectThread,
}: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  return (
    <section className="agentos-conversation-list flex h-full min-h-0 min-w-0 flex-col overflow-hidden border border-zinc-200 bg-white">
      <header className="border-b border-zinc-200 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-zinc-950">会话</p>
          <button
            type="button"
            onClick={onNewConversation}
            disabled={isChatStreaming}
            className="border border-zinc-300 px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition hover:border-zinc-500 hover:text-zinc-950 disabled:cursor-not-allowed disabled:opacity-40"
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

                return (
                  <button
                    key={conversation.id}
                    type="button"
                    onClick={() => onSelectThread(conversation.id)}
                    disabled={isChatStreaming}
                    aria-current={active ? "page" : undefined}
                    className={`block w-full min-w-0 px-3 py-2.5 text-left transition disabled:cursor-not-allowed ${
                      active ? "bg-zinc-900 text-white" : "text-zinc-900 hover:bg-zinc-50"
                    }`}
                  >
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <p className="min-w-0 truncate text-sm font-medium">
                        {conversationLabel(conversation)}
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
                );
              })}
            </section>
          ))}
        </nav>
      )}
    </section>
  );
}
