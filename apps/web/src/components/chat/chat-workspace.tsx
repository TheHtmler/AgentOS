"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { InvitationManager } from "@/components/auth/invitation-manager";
import { AgentOsLogo } from "@/components/brand/agentos-logo";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ConversationList } from "@/components/chat/conversation-list";
import { PendingCaseFactsBanner } from "@/components/chat/pending-case-facts-banner";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { parseAgentSummaries, type AgentSummary } from "@/lib/agents";
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
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
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
        setSelectedAgentId((current) => {
          if (current !== null && nextAgents.some((agent) => agent.id === current)) {
            return current;
          }
          return nextAgents.find((agent) => agent.is_default)?.id ?? nextAgents[0]?.id ?? null;
        });
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

  useEffect(() => {
    if (!isMobileMenuOpen) {
      return;
    }

    const previousBodyOverflow = document.body.style.overflow;
    const previousHtmlOverflow = document.documentElement.style.overflow;

    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsMobileMenuOpen(false);
      }
    };

    window.addEventListener("keydown", closeOnEscape);

    return () => {
      document.body.style.overflow = previousBodyOverflow;
      document.documentElement.style.overflow = previousHtmlOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [isMobileMenuOpen]);

  const focusSlot = useCallback((slotKey: string, threadId: string | null) => {
    setVisibleSlotKey(slotKey);
    setActiveThreadId(threadId);
    setIsMobileMenuOpen(false);

    if (threadId === null) {
      clearThreadFromUrl();
    } else {
      setThreadInUrl(threadId);
    }
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

      if (agentId !== undefined) {
        setSelectedAgentId(agentId);
      }

      if (slotKey === visibleSlotKeyRef.current) {
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

          <AgentOsLogo subtitle="助手工作台" />

          <div className="ml-auto hidden items-center gap-3 lg:flex">
            <ThemeToggle />
            <span className="agentos-runtime-tag">本地服务 · 已就绪</span>
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
            <span className="agentos-runtime-tag">已就绪</span>
          </div>
        </div>
      </header>

      <div className="agentos-workspace">
        <aside className="agentos-conversation-rail hidden min-h-0 lg:flex lg:flex-col">
          <ConversationList
            activeThreadId={activeThreadId}
            agents={agents}
            selectedAgentId={selectedAgentId}
            refreshKey={threadListVersion}
            streamingThreadIds={streamingThreadIds}
            awaitingApprovalThreadIds={awaitingApprovalThreadIds}
            onNewConversation={handleNewConversation}
            onSelectAgent={handleSelectAgent}
            onSelectThread={handleSelectThread}
            onThreadDeleted={handleThreadDeleted}
          />
        </aside>

        <section className="agentos-main-column min-h-0">
          <PendingCaseFactsBanner
            agentId={selectedAgentId}
            caseEnabled={
              agents.find((agent) => agent.id === selectedAgentId)?.case_enabled ?? false
            }
            refreshKey={threadListVersion}
          />
          {hasHydratedFromUrl
            ? slots.map((slot) => {
                const isActive = slot.key === visibleSlotKey;

                return (
                  <div
                    key={slot.key}
                    className={isActive ? "h-full min-h-0" : "hidden"}
                    aria-hidden={!isActive}
                  >
                    <ChatPanel
                      selectedThreadId={slot.threadId}
                      agentId={selectedAgentId}
                      agentLoadError={agentLoadError}
                      isActive={isActive}
                      onRetryAgentLoad={retryAgentLoad}
                      onNewConversation={handleNewConversation}
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
        </section>
      </div>

      {isMobileMenuOpen ? (
        <div className="agentos-mobile-menu lg:hidden">
          <button
            type="button"
            className="agentos-mobile-menu-backdrop"
            onClick={() => setIsMobileMenuOpen(false)}
            aria-label="关闭主菜单"
          />

          <aside
            className="agentos-mobile-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mobile-menu-title"
          >
            <header className="agentos-mobile-drawer-header">
              <p id="mobile-menu-title" className="text-sm font-semibold text-zinc-950">
                会话与工作区
              </p>
              <button
                type="button"
                onClick={() => setIsMobileMenuOpen(false)}
                className="agentos-mobile-close"
                aria-label="关闭主菜单"
              >
                关闭
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-hidden">
              <ConversationList
                activeThreadId={activeThreadId}
                agents={agents}
                selectedAgentId={selectedAgentId}
                refreshKey={threadListVersion}
                streamingThreadIds={streamingThreadIds}
                awaitingApprovalThreadIds={awaitingApprovalThreadIds}
                onNewConversation={handleNewConversation}
                onSelectAgent={handleSelectAgent}
                onSelectThread={handleSelectThread}
                onThreadDeleted={handleThreadDeleted}
              />
            </div>

            <footer className="agentos-mobile-account">
              <p className="truncate text-sm font-medium text-zinc-950" title={userEmail}>
                {userEmail}
              </p>
              <p className="mt-1 text-xs text-zinc-500">当前已登录</p>
              <div className="mt-4 space-y-2">
                {canManageInvitations ? <InvitationManager /> : null}
                <button
                  type="button"
                  onClick={onLogout}
                  disabled={isLoggingOut}
                  className="agentos-mobile-logout disabled:cursor-not-allowed disabled:opacity-50"
                >
                  退出登录
                </button>
              </div>
            </footer>
          </aside>
        </div>
      ) : null}
    </>
  );
}
