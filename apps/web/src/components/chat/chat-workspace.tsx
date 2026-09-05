"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarClock,
  Link2,
  LogOut,
  MessageCircle,
  MessageSquare,
  SquarePen,
} from "lucide-react";

import { InvitationManager } from "@/components/auth/invitation-manager";
import { AgentOsLogo } from "@/components/brand/agentos-logo";
import { AgentSelector } from "@/components/chat/agent-selector";
import { AssistantThread } from "@/components/chat/assistant-thread";
import { DataManagementDialog } from "@/components/account/data-management-dialog";
import { ConversationList } from "@/components/chat/conversation-list";
import { PendingCaseFactsBanner } from "@/components/chat/pending-case-facts-banner";
import { ScheduledTasksPanel } from "@/components/chat/scheduled-tasks-panel";
import { WeChatBindingPanel } from "@/components/channel/wechat-binding-panel";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { parseAgentSummaries, resolveSelectedAgentId, type AgentSummary } from "@/lib/agents";
import type { Conversation } from "@/components/chat/conversation-list";

type ChatWorkspaceProps = {
  userEmail: string;
  canManageInvitations: boolean;
  isLoggingOut: boolean;
  onLogout: () => void;
};

type ChatSlot = {
  key: string;
  /** null = blank draft; string = bound Thread */
  threadId: string | null;
};

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

const MAX_IDLE_SLOTS = 8;

function createSlotKey(): string {
  return crypto.randomUUID();
}

/** ChatPanel still reports run starts; the workspace no longer tracks them. */
function ignoreRunStarted() {}

function setThreadInUrl(threadId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("thread", threadId);
  window.history.replaceState(window.history.state, "", url);
}

function clearThreadFromUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("thread");
  window.history.replaceState(window.history.state, "", url);
}

function pruneIdleSlots(
  slots: ChatSlot[],
  visibleSlotKey: string,
  streamingBySlotKey: Record<string, boolean>,
): ChatSlot[] {
  const keep = slots.filter(
    (slot) => slot.key === visibleSlotKey || Boolean(streamingBySlotKey[slot.key]),
  );
  const idle = slots.filter((slot) => slot.key !== visibleSlotKey && !streamingBySlotKey[slot.key]);

  if (idle.length <= MAX_IDLE_SLOTS) {
    return slots;
  }

  return [...keep, ...idle.slice(idle.length - MAX_IDLE_SLOTS)];
}

export function ChatWorkspace({
  userEmail,
  canManageInvitations,
  isLoggingOut,
  onLogout,
}: ChatWorkspaceProps) {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [activeView, setActiveView] = useState<"chat" | "scheduled" | "wechat">("chat");
  const [scheduledUnreadCount, setScheduledUnreadCount] = useState(0);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState(() => resolveSelectedAgentId(null, []));
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);
  const [agentLoadAttempt, setAgentLoadAttempt] = useState(0);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadListVersion, setThreadListVersion] = useState(0);
  const [slots, setSlots] = useState<ChatSlot[]>([{ key: "boot", threadId: null }]);
  const [visibleSlotKey, setVisibleSlotKey] = useState("boot");
  const [streamingBySlotKey, setStreamingBySlotKey] = useState<Record<string, boolean>>({});
  const [awaitingApprovalBySlotKey, setAwaitingApprovalBySlotKey] = useState<
    Record<string, boolean>
  >({});
  const [hasHydratedFromUrl, setHasHydratedFromUrl] = useState(false);

  const streamingBySlotKeyRef = useRef(streamingBySlotKey);
  const slotsRef = useRef(slots);
  const visibleSlotKeyRef = useRef(visibleSlotKey);

  useEffect(() => {
    streamingBySlotKeyRef.current = streamingBySlotKey;
    slotsRef.current = slots;
    visibleSlotKeyRef.current = visibleSlotKey;
  }, [slots, streamingBySlotKey, visibleSlotKey]);

  useEffect(() => {
    const controller = new AbortController();
    let isCurrent = true;

    void (async () => {
      try {
        setAgentLoadError(null);
        const response = await fetch("/api/agents", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error("无法加载助手列表。");
        }

        const nextAgents = parseAgentSummaries((await response.json()) as unknown);
        if (nextAgents === null) {
          throw new Error("助手列表格式无效。");
        }
        if (!isCurrent) {
          return;
        }

        setAgents(nextAgents);
        setSelectedAgentId((current) => resolveSelectedAgentId(current, nextAgents));
      } catch (error: unknown) {
        if (isCurrent && !controller.signal.aborted) {
          setAgentLoadError(error instanceof Error ? error.message : "无法加载助手列表。");
        }
      }
    })();

    return () => {
      isCurrent = false;
      controller.abort();
    };
  }, [agentLoadAttempt]);

  const retryAgentLoad = useCallback(() => {
    setAgentLoadAttempt((current) => current + 1);
  }, []);

  const streamingThreadIds = useMemo(() => {
    const ids = new Set<string>();

    for (const slot of slots) {
      if (streamingBySlotKey[slot.key] && typeof slot.threadId === "string") {
        ids.add(slot.threadId);
      }
    }

    return ids;
  }, [slots, streamingBySlotKey]);

  const awaitingApprovalThreadIds = useMemo(() => {
    const ids = new Set<string>();

    for (const slot of slots) {
      if (awaitingApprovalBySlotKey[slot.key] && typeof slot.threadId === "string") {
        ids.add(slot.threadId);
      }
    }

    return ids;
  }, [slots, awaitingApprovalBySlotKey]);

  useEffect(() => {
    if (hasHydratedFromUrl) {
      return;
    }

    const threadFromUrl = new URL(window.location.href).searchParams.get("thread");

    /* eslint-disable react-hooks/set-state-in-effect */
    if (threadFromUrl !== null && isUuid(threadFromUrl)) {
      setSlots([{ key: threadFromUrl, threadId: threadFromUrl }]);
      setVisibleSlotKey(threadFromUrl);
      setActiveThreadId(threadFromUrl);
    } else {
      const key = createSlotKey();
      setSlots([{ key, threadId: null }]);
      setVisibleSlotKey(key);
      setActiveThreadId(null);
    }

    setHasHydratedFromUrl(true);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [hasHydratedFromUrl]);

  const focusSlot = useCallback((slotKey: string, threadId: string | null) => {
    // History loads complete asynchronously in mounted background slots. Update
    // this ref before scheduling React state so their callbacks cannot reclaim
    // the active Agent during a conversation/Agent switch.
    visibleSlotKeyRef.current = slotKey;
    setActiveView("chat");
    setVisibleSlotKey(slotKey);
    setActiveThreadId(threadId);
    setIsMobileMenuOpen(false);

    if (threadId === null) {
      clearThreadFromUrl();
    } else {
      setThreadInUrl(threadId);
    }
  }, []);

  const handleOpenTaskThread = useCallback(
    (threadId: string, agentId?: string) => {
      if (agentId !== undefined) {
        setSelectedAgentId(agentId);
      }
      const currentSlots = slotsRef.current;
      const existing = currentSlots.find((slot) => slot.threadId === threadId);
      if (existing) {
        focusSlot(existing.key, threadId);
        return;
      }
      const nextSlot: ChatSlot = { key: threadId, threadId };
      setSlots(
        pruneIdleSlots([...currentSlots, nextSlot], nextSlot.key, streamingBySlotKeyRef.current),
      );
      focusSlot(nextSlot.key, threadId);
    },
    [focusSlot],
  );

  const handleScheduledUnreadCountChange = useCallback((count: number) => {
    setScheduledUnreadCount(count);
  }, []);

  const handleSelectThread = useCallback(
    (conversation: Conversation) => {
      const { id: threadId, agent_id: agentId } = conversation;
      setSelectedAgentId(agentId);
      const currentSlots = slotsRef.current;
      const existing = currentSlots.find((slot) => slot.threadId === threadId);

      if (existing) {
        focusSlot(existing.key, threadId);
        return;
      }

      const nextSlot: ChatSlot = { key: threadId, threadId };
      setSlots(
        pruneIdleSlots([...currentSlots, nextSlot], nextSlot.key, streamingBySlotKeyRef.current),
      );
      focusSlot(nextSlot.key, threadId);
    },
    [focusSlot],
  );

  const handleNewConversation = useCallback(() => {
    const currentSlots = slotsRef.current;
    const emptyDraft = currentSlots.find(
      (slot) => slot.threadId === null && !streamingBySlotKeyRef.current[slot.key],
    );

    if (emptyDraft) {
      focusSlot(emptyDraft.key, null);
      return;
    }

    const key = createSlotKey();
    setSlots(
      pruneIdleSlots(
        [...currentSlots, { key, threadId: null }],
        key,
        streamingBySlotKeyRef.current,
      ),
    );
    focusSlot(key, null);
  }, [focusSlot]);

  const handleSelectAgent = useCallback(
    (agentId: string) => {
      if (agentId === selectedAgentId) {
        return;
      }
      setSelectedAgentId(agentId);
      handleNewConversation();
    },
    [handleNewConversation, selectedAgentId],
  );

  const handleSlotThreadChanged = useCallback(
    (slotKey: string, threadId: string | null, agentId?: string) => {
      setSlots((current) =>
        current.map((slot) => (slot.key === slotKey ? { ...slot, threadId } : slot)),
      );

      if (slotKey === visibleSlotKeyRef.current) {
        if (agentId !== undefined) {
          setSelectedAgentId(agentId);
        }
        setActiveThreadId(threadId);

        if (threadId === null) {
          clearThreadFromUrl();
        } else {
          setThreadInUrl(threadId);
        }
      }

      setThreadListVersion((current) => current + 1);
    },
    [],
  );

  const handleSlotStreamingChanged = useCallback((slotKey: string, isStreaming: boolean) => {
    setStreamingBySlotKey((current) => {
      if (Boolean(current[slotKey]) === isStreaming) {
        return current;
      }

      return { ...current, [slotKey]: isStreaming };
    });
  }, []);

  const handleSlotAwaitingApprovalChanged = useCallback((slotKey: string, isAwaiting: boolean) => {
    setAwaitingApprovalBySlotKey((current) => {
      if (Boolean(current[slotKey]) === isAwaiting) {
        return current;
      }

      return { ...current, [slotKey]: isAwaiting };
    });
  }, []);

  const handleRunFinalized = useCallback(() => {
    setThreadListVersion((current) => current + 1);
  }, []);

  const handleThreadListChanged = useCallback(() => {
    setThreadListVersion((current) => current + 1);
  }, []);

  const handleThreadDeleted = useCallback(
    (threadId: string) => {
      setThreadListVersion((current) => current + 1);

      const currentSlots = slotsRef.current;
      const deletedKeys = currentSlots
        .filter((slot) => slot.threadId === threadId)
        .map((slot) => slot.key);
      const remaining = currentSlots.filter((slot) => slot.threadId !== threadId);
      const deletedVisible = deletedKeys.includes(visibleSlotKeyRef.current);

      setStreamingBySlotKey((current) => {
        const next = { ...current };
        for (const key of deletedKeys) {
          delete next[key];
        }
        return next;
      });

      setAwaitingApprovalBySlotKey((current) => {
        const next = { ...current };
        for (const key of deletedKeys) {
          delete next[key];
        }
        return next;
      });

      if (!deletedVisible) {
        setSlots(remaining.length > 0 ? remaining : [{ key: createSlotKey(), threadId: null }]);
        return;
      }

      const fallback =
        remaining.find(
          (slot) => slot.threadId === null && !streamingBySlotKeyRef.current[slot.key],
        ) ?? remaining[0];

      if (fallback) {
        setSlots(remaining);
        focusSlot(fallback.key, fallback.threadId ?? null);
        return;
      }

      const key = createSlotKey();
      setSlots([{ key, threadId: null }]);
      focusSlot(key, null);
    },
    [focusSlot],
  );

  return (
    <>
      <header className="agentos-topbar">
        <div className="agentos-topbar-inner">
          <button
            type="button"
            onClick={() => setIsMobileMenuOpen(true)}
            className="agentos-mobile-menu-toggle lg:hidden"
            aria-label="打开主菜单"
            aria-expanded={isMobileMenuOpen}
          >
            <span />
            <span />
            <span />
          </button>

          <AgentOsLogo className="agentos-mobile-agentos-brand" />

          <div className="ml-auto hidden items-center gap-3 lg:flex">
            <ThemeToggle compact />
            <p className="max-w-48 truncate text-sm text-zinc-600" title={userEmail}>
              {userEmail}
            </p>
            {canManageInvitations ? <InvitationManager /> : null}
            <button
              type="button"
              onClick={onLogout}
              disabled={isLoggingOut}
              className="agentos-header-action disabled:cursor-not-allowed disabled:opacity-50"
            >
              退出
            </button>
          </div>

          <div className="ml-auto flex items-center gap-2 lg:hidden">
            <ThemeToggle compact />
          </div>
        </div>
      </header>

      <div className="agentos-workspace">
        <aside className="agentos-conversation-rail hidden min-h-0 lg:flex lg:flex-col">
          <div className="agentos-codex-sidebar">
            <header className="agentos-codex-sidebar-header">
              <AgentOsLogo className="agentos-codex-brand" />
            </header>

            <nav className="agentos-codex-primary-nav" aria-label="主导航">
              <button type="button" onClick={handleNewConversation}>
                <SquarePen aria-hidden="true" className="size-4" />
                新建任务
              </button>
              <button
                type="button"
                className={activeView === "chat" ? "is-active" : undefined}
                onClick={() => setActiveView("chat")}
              >
                <MessageSquare aria-hidden="true" className="size-4" />
                聊天
              </button>
              <button
                type="button"
                className={activeView === "scheduled" ? "is-active" : undefined}
                onClick={() => {
                  setActiveView("scheduled");
                  setIsMobileMenuOpen(false);
                }}
              >
                <CalendarClock aria-hidden="true" className="size-4" />
                定时任务
                {scheduledUnreadCount > 0 ? (
                  <span className="ml-auto grid min-w-5 place-items-center rounded-full bg-[var(--accent)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--send-fg)]">
                    {scheduledUnreadCount}
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                className={activeView === "wechat" ? "is-active" : undefined}
                onClick={() => {
                  setActiveView("wechat");
                  setIsMobileMenuOpen(false);
                }}
              >
                <MessageCircle aria-hidden="true" className="size-4" />
                微信通知
              </button>
            </nav>

            <div className="agentos-codex-sidebar-section agentos-codex-pinned-section">
              <div className="agentos-codex-sidebar-section-heading">
                <p>已固定</p>
              </div>
              <div className="agentos-codex-pinned-host">
                <ConversationList
                  activeThreadId={activeThreadId}
                  refreshKey={threadListVersion}
                  streamingThreadIds={streamingThreadIds}
                  awaitingApprovalThreadIds={awaitingApprovalThreadIds}
                  onNewConversation={handleNewConversation}
                  onSelectThread={handleSelectThread}
                  onThreadDeleted={handleThreadDeleted}
                  onThreadListChanged={handleThreadListChanged}
                  pinnedOnly
                />
              </div>
            </div>

            <div className="agentos-codex-sidebar-section agentos-codex-tasks-section">
              <div className="agentos-codex-conversation-host">
                <ConversationList
                  activeThreadId={activeThreadId}
                  refreshKey={threadListVersion}
                  streamingThreadIds={streamingThreadIds}
                  awaitingApprovalThreadIds={awaitingApprovalThreadIds}
                  onNewConversation={handleNewConversation}
                  onSelectThread={handleSelectThread}
                  onThreadDeleted={handleThreadDeleted}
                  onThreadListChanged={handleThreadListChanged}
                />
              </div>
            </div>

            <footer className="agentos-codex-sidebar-footer">
              <span className="agentos-codex-account-dot" aria-hidden="true" />
              <span className="truncate" title={userEmail}>
                {userEmail}
              </span>
              {canManageInvitations ? <InvitationManager /> : null}
              <DataManagementDialog />
              <ThemeToggle compact />
              <button
                type="button"
                onClick={onLogout}
                disabled={isLoggingOut}
                aria-label="退出登录"
              >
                <LogOut aria-hidden="true" className="size-4" />
              </button>
            </footer>
          </div>
        </aside>

        <section className="agentos-main-column min-h-0">
          <div className={activeView === "scheduled" ? "h-full min-h-0" : "hidden"}>
            <ScheduledTasksPanel
              agents={agents}
              onOpenThread={handleOpenTaskThread}
              onUnreadCountChange={handleScheduledUnreadCountChange}
            />
          </div>
          <div className={activeView === "wechat" ? "h-full min-h-0" : "hidden"}>
            <WeChatBindingPanel />
          </div>
          <div className={activeView === "chat" ? "h-full min-h-0" : "hidden"}>
            <>
              <PendingCaseFactsBanner
                agentId={selectedAgentId}
                caseEnabled={
                  agents.find((agent) => agent.id === selectedAgentId)?.case_enabled ?? false
                }
                refreshKey={threadListVersion}
              />
              {agentLoadError !== null ? (
                <div
                  role="alert"
                  className="mx-auto flex w-full max-w-3xl items-center gap-3 border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-sm text-destructive"
                >
                  <span>{agentLoadError}，将使用默认助手继续对话。</span>
                  <button type="button" onClick={retryAgentLoad} className="font-medium underline">
                    重试
                  </button>
                </div>
              ) : null}
              {hasHydratedFromUrl
                ? slots.map((slot) => {
                    const isActive = slot.key === visibleSlotKey;

                    return (
                      <div
                        key={slot.key}
                        className={isActive ? "h-full min-h-0" : "hidden"}
                        aria-hidden={!isActive}
                      >
                        <AssistantThread
                          selectedThreadId={slot.threadId}
                          agentId={selectedAgentId}
                          composerFooter={
                            <AgentSelector
                              agents={agents}
                              value={selectedAgentId}
                              onChange={handleSelectAgent}
                            />
                          }
                          isActive={isActive}
                          onRunStarted={ignoreRunStarted}
                          onStreamingChanged={(isStreaming) =>
                            handleSlotStreamingChanged(slot.key, isStreaming)
                          }
                          onAwaitingApprovalChanged={(isAwaiting) =>
                            handleSlotAwaitingApprovalChanged(slot.key, isAwaiting)
                          }
                          onThreadChanged={(threadId, agentId) =>
                            handleSlotThreadChanged(slot.key, threadId, agentId)
                          }
                          onRunFinalized={handleRunFinalized}
                        />
                      </div>
                    );
                  })
                : null}
            </>
          </div>
        </section>
      </div>

      <Sheet open={isMobileMenuOpen} onOpenChange={setIsMobileMenuOpen}>
        <SheetContent className="lg:hidden" showCloseButton={false}>
          <SheetHeader className="flex min-h-14 flex-row items-center justify-between gap-3">
            <div>
              <SheetTitle>会话与工作区</SheetTitle>
              <SheetDescription className="sr-only">切换会话、定时任务和通知设置</SheetDescription>
            </div>
            <button
              type="button"
              onClick={() => setIsMobileMenuOpen(false)}
              className="inline-flex min-h-11 items-center rounded-md px-3 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              关闭
            </button>
          </SheetHeader>

          <div className="min-h-0 flex-1 overflow-hidden p-3">
            <nav className="mb-3 grid gap-1" aria-label="主导航">
              <button
                type="button"
                onClick={handleNewConversation}
                className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-sm text-foreground hover:bg-muted"
              >
                <SquarePen aria-hidden="true" className="size-4" />
                新建任务
              </button>
              <button
                type="button"
                aria-current={activeView === "scheduled" ? "page" : undefined}
                className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-sm text-foreground hover:bg-muted aria-[current=page]:bg-muted"
                onClick={() => {
                  setActiveView("scheduled");
                  setIsMobileMenuOpen(false);
                }}
              >
                <CalendarClock aria-hidden="true" className="size-4" />
                定时任务
                {scheduledUnreadCount > 0 ? (
                  <span className="ml-auto grid min-w-5 place-items-center rounded-full bg-primary px-1.5 py-0.5 text-[11px] font-semibold text-primary-foreground">
                    {scheduledUnreadCount}
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                aria-current={activeView === "wechat" ? "page" : undefined}
                className="flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-sm text-foreground hover:bg-muted aria-[current=page]:bg-muted"
                onClick={() => {
                  setActiveView("wechat");
                  setIsMobileMenuOpen(false);
                }}
              >
                <Link2 aria-hidden="true" className="size-4" />
                微信通知
              </button>
            </nav>
            <ConversationList
              activeThreadId={activeThreadId}
              refreshKey={threadListVersion}
              streamingThreadIds={streamingThreadIds}
              awaitingApprovalThreadIds={awaitingApprovalThreadIds}
              onNewConversation={handleNewConversation}
              onSelectThread={handleSelectThread}
              onThreadDeleted={handleThreadDeleted}
              onThreadListChanged={handleThreadListChanged}
            />
          </div>

          <footer className="border-t border-border p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
            <p className="truncate text-sm font-medium text-foreground" title={userEmail}>
              {userEmail}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">当前已登录</p>
            <div className="mt-4 grid gap-2">
              {canManageInvitations ? <InvitationManager /> : null}
              <button
                type="button"
                onClick={onLogout}
                disabled={isLoggingOut}
                className="inline-flex min-h-11 w-full items-center justify-center rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              >
                退出登录
              </button>
            </div>
          </footer>
        </SheetContent>
      </Sheet>
    </>
  );
}
