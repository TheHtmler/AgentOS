"use client";

import { useEffect, useState } from "react";

type Conversation = {
  id: string;
  title: string | null;
  latest_message_content: string | null;
  updated_at: string;
};

type ConversationListProps = {
  activeThreadId: string | null;
  refreshKey: number;
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

function formatUpdatedAt(value: string): string {
  const date = new Date(value);

  if (!Number.isFinite(date.getTime())) {
    return "";
  }

  return date.toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}

function conversationLabel(conversation: Conversation): string {
  const source = conversation.title ?? conversation.latest_message_content ?? "新会话";
  return source.replace(/\s+/g, " ").trim().slice(0, 40) || "新会话";
}

export function ConversationList({ activeThreadId, refreshKey }: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
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

  return (
    <section className="border border-zinc-200 bg-white">
      <header className="border-b border-zinc-200 px-4 py-4">
        <p className="text-sm font-semibold text-zinc-950">最近会话</p>
      </header>

      {error ? (
        <p role="alert" className="px-4 py-4 text-sm text-rose-700">
          {error}
        </p>
      ) : conversations.length === 0 ? (
        <p className="px-4 py-4 text-sm text-zinc-500">暂无已保存的会话。</p>
      ) : (
        <nav aria-label="最近会话" className="max-h-80 overflow-y-auto lg:max-h-[calc(100vh-8rem)]">
          {conversations.map((conversation) => {
            const active = conversation.id === activeThreadId;
            const preview = conversation.latest_message_content ?? "暂无消息";

            return (
              <a
                key={conversation.id}
                href={`/?thread=${conversation.id}`}
                aria-current={active ? "page" : undefined}
                className={`block border-b border-zinc-100 px-4 py-3 last:border-b-0 ${
                  active ? "bg-zinc-100" : "hover:bg-zinc-50"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 truncate text-sm font-medium text-zinc-900">
                    {conversationLabel(conversation)}
                  </p>
                  <time
                    className="shrink-0 text-xs text-zinc-400"
                    dateTime={conversation.updated_at}
                  >
                    {formatUpdatedAt(conversation.updated_at)}
                  </time>
                </div>
                <p className="mt-1 truncate text-xs text-zinc-500">{preview}</p>
              </a>
            );
          })}
        </nav>
      )}
    </section>
  );
}
