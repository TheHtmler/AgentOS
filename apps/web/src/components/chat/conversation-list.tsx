"use client";

import { FormEvent, MouseEvent, useEffect, useMemo, useState } from "react";
import { CalendarClock, MoreHorizontal, Plus } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type Conversation = {
  id: string;
  agent_id: string;
  title: string | null;
  is_pinned: boolean;
  latest_message_content: string | null;
  updated_at: string;
  scheduled_task_id: string | null;
  scheduled_task_title: string | null;
};

type ConversationListProps = {
  activeThreadId: string | null;
  refreshKey: number;
  streamingThreadIds: ReadonlySet<string>;
  awaitingApprovalThreadIds?: ReadonlySet<string>;
  onNewConversation: () => void;
  onSelectThread: (conversation: Conversation) => void;
  onThreadDeleted: (threadId: string) => void;
  onThreadListChanged: () => void;
  pinnedOnly?: boolean;
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
    typeof value.is_pinned === "boolean" &&
    (typeof value.latest_message_content === "string" || value.latest_message_content === null) &&
    typeof value.updated_at === "string" &&
    (typeof value.scheduled_task_id === "string" || value.scheduled_task_id === null) &&
    (typeof value.scheduled_task_title === "string" || value.scheduled_task_title === null)
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
  awaitingApprovalThreadIds = new Set<string>(),
  onNewConversation,
  onSelectThread,
  onThreadDeleted,
  onThreadListChanged,
  pinnedOnly = false,
}: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [menuThreadId, setMenuThreadId] = useState<string | null>(null);
  const [renamingThreadId, setRenamingThreadId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [busyThreadId, setBusyThreadId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    void (async () => {
      try {
        const query = new URLSearchParams({ limit: "50" });
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
          setIsLoading(false);
        }
      } catch (caughtError: unknown) {
        if (!isCurrent || controller.signal.aborted) {
          return;
        }

        setError(caughtError instanceof Error ? caughtError.message : "无法读取最近会话。");
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
  }, [refreshKey]);

  const isLoadingCurrentAgent = isLoading;
  const currentError = error;

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

    return groupConversations(
      pinnedOnly
        ? filtered.filter((conversation) => conversation.is_pinned)
        : filtered.filter((conversation) => !conversation.is_pinned),
    );
  }, [conversations, pinnedOnly, query]);

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
      onThreadListChanged();
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "重命名失败，请稍后重试。");
    } finally {
      setBusyThreadId(null);
    }
  }

  async function togglePinned(conversation: Conversation) {
    setMenuThreadId(null);
    setBusyThreadId(conversation.id);

    try {
      const response = await fetch(`/api/threads/${conversation.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_pinned: !conversation.is_pinned }),
      });

      if (!response.ok) {
        throw new Error(
          conversation.is_pinned ? "取消固定失败，请稍后重试。" : "固定失败，请稍后重试。",
        );
      }

      const payload: unknown = await response.json();
      if (!isRecord(payload) || typeof payload.is_pinned !== "boolean") {
        throw new Error("固定状态响应无效。");
      }

      setConversations((current) =>
        current.map((item) =>
          item.id === conversation.id ? { ...item, is_pinned: payload.is_pinned as boolean } : item,
        ),
      );
      setError(null);
      onThreadListChanged();
    } catch (caughtError: unknown) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : conversation.is_pinned
            ? "取消固定失败，请稍后重试。"
            : "固定失败，请稍后重试。",
      );
    } finally {
      setBusyThreadId(null);
    }
  }

  function requestDeleteThread(conversation: Conversation) {
    setMenuThreadId(null);
    setDeleteTarget(conversation);
  }

  async function deleteThread(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    const conversation = deleteTarget;
    if (conversation === null) {
      return;
    }

    setBusyThreadId(conversation.id);

    try {
      const response = await fetch(`/api/threads/${conversation.id}`, { method: "DELETE" });

      if (!response.ok) {
        throw new Error("删除失败，请稍后重试。");
      }

      setConversations((current) => current.filter((item) => item.id !== conversation.id));
      setDeleteTarget(null);
      setError(null);
      onThreadDeleted(conversation.id);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "删除失败，请稍后重试。");
    } finally {
      setBusyThreadId(null);
    }
  }

  return (
    <section className="agentos-conversation-list max-w-full min-w-0 overflow-x-hidden">
      {!pinnedOnly ? (
        <header className="agentos-conversation-list-header">
          <div className="agentos-conversation-toolbar">
            <p className="agentos-list-title">会话</p>
            <button
              type="button"
              onClick={onNewConversation}
              className="agentos-list-new-button disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Plus aria-hidden="true" className="size-3.5" />
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
      ) : null}

      {currentError ? (
        <p role="alert" className="agentos-list-feedback is-error">
          {currentError}
        </p>
      ) : isLoadingCurrentAgent ? (
        <p className="agentos-list-feedback">读取会话中…</p>
      ) : groups.length === 0 ? (
        <div className="agentos-list-feedback">
          <p>
            {query.trim() ? "没有匹配的已加载会话。" : pinnedOnly ? "暂无固定会话。" : "暂无会话。"}
          </p>
          {!pinnedOnly && !query.trim() ? (
            <button
              type="button"
              onClick={onNewConversation}
              className="agentos-list-empty-action disabled:cursor-not-allowed disabled:opacity-50"
            >
              新建会话
            </button>
          ) : null}
        </div>
      ) : (
        <nav
          aria-label="最近会话"
          className="agentos-conversation-list-scroll min-h-0 max-w-full min-w-0 flex-1 overflow-y-auto"
        >
          {groups.map((group) => (
            <section key={group.label} className="agentos-conversation-group max-w-full min-w-0">
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
                    className={`agentos-conversation-item max-w-full min-w-0 ${active ? "is-active" : ""}`}
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
                      <div className="agentos-conversation-row max-w-full min-w-0">
                        <button
                          type="button"
                          onClick={() => onSelectThread(conversation)}
                          disabled={isBusy}
                          aria-current={active ? "page" : undefined}
                          className="agentos-conversation-button max-w-full min-w-0 disabled:cursor-not-allowed"
                        >
                          <div className="agentos-conversation-title-row max-w-full min-w-0">
                            <p className="agentos-conversation-title max-w-full min-w-0">
                              {conversation.scheduled_task_id ? (
                                <span
                                  title={
                                    conversation.scheduled_task_title
                                      ? `定时任务：${conversation.scheduled_task_title}`
                                      : "定时任务会话"
                                  }
                                  aria-label="定时任务会话"
                                  className="mr-1 inline-flex align-[-0.1em] text-[var(--accent)]"
                                >
                                  <CalendarClock aria-hidden="true" className="size-3" />
                                </span>
                              ) : null}
                              <span
                                className={`agentos-conversation-title-text ${
                                  conversationLabel(conversation).length > 18 ? "is-scrollable" : ""
                                }`}
                              >
                                {conversationLabel(conversation)}
                              </span>
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
                          <p className="agentos-conversation-preview max-w-full min-w-0 overflow-hidden">
                            {preview}
                          </p>
                        </button>

                        <DropdownMenu
                          open={menuThreadId === conversation.id}
                          onToggle={(event) =>
                            setMenuThreadId(event.currentTarget.open ? conversation.id : null)
                          }
                        >
                          <DropdownMenuTrigger
                            aria-label="会话操作"
                            aria-disabled={isStreaming || isBusy}
                            onClick={(event) => {
                              if (isStreaming || isBusy) event.preventDefault();
                            }}
                            className="flex size-9 cursor-pointer items-center justify-center text-muted-foreground hover:bg-muted hover:text-foreground aria-disabled:pointer-events-none aria-disabled:opacity-40"
                          >
                            <MoreHorizontal aria-hidden="true" className="size-4" />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem onClick={() => beginRename(conversation)}>
                              重命名
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => void togglePinned(conversation)}>
                              {conversation.is_pinned ? "取消固定" : "固定会话"}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="text-destructive hover:bg-destructive/10"
                              onClick={() => requestDeleteThread(conversation)}
                            >
                              删除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    )}
                  </div>
                );
              })}
            </section>
          ))}
        </nav>
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && busyThreadId === null) {
            setDeleteTarget(null);
          }
        }}
      >
        <AlertDialogContent className="agentos-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话？</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleteTarget === null ? "当前会话" : conversationLabel(deleteTarget)}
              ”删除后将从会话列表中移除，无法恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={busyThreadId !== null}
              onClick={() => setDeleteTarget(null)}
              className="agentos-delete-dialog-cancel"
            >
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={busyThreadId !== null}
              onClick={(event) => void deleteThread(event)}
              className="agentos-delete-dialog-action"
            >
              {busyThreadId !== null ? "删除中…" : "删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
