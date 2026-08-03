"use client";

import { useCallback, useEffect, useState } from "react";

import { InvitationManager } from "@/components/auth/invitation-manager";
import { AgentOsLogo } from "@/components/brand/agentos-logo";
import { ChatPanel } from "@/components/chat/chat-panel";
import { ConversationList } from "@/components/chat/conversation-list";
import { RunInspector } from "@/components/run/run-inspector";
import { HealthStatus } from "@/components/system/health-status";

type ChatWorkspaceProps = {
  userEmail: string;
  canManageInvitations: boolean;
  isLoggingOut: boolean;
  onLogout: () => void;
};

const runtimeItems = [
  { label: "执行入口", value: "FastAPI Agent API" },
  { label: "模型路由", value: "Ollama · agentos-gemma4:8k" },
  { label: "会话存储", value: "PostgreSQL Thread" },
];

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

export function ChatWorkspace({
  userEmail,
  canManageInvitations,
  isLoggingOut,
  onLogout,
}: ChatWorkspaceProps) {
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadListVersion, setThreadListVersion] = useState(0);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null | undefined>(undefined);
  const [chatViewKey, setChatViewKey] = useState(0);
  const [isChatStreaming, setIsChatStreaming] = useState(false);
  const [isRuntimeRailOpen, setIsRuntimeRailOpen] = useState(false);

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

  const handleSelectThread = useCallback((threadId: string) => {
    // Remounting ChatPanel aborts any in-flight run for the previous Thread.
    setThreadInUrl(threadId);
    setActiveThreadId(threadId);
    setActiveRunId(null);
    setSelectedThreadId(threadId);
    setIsMobileMenuOpen(false);
    setIsChatStreaming(false);
    setChatViewKey((current) => current + 1);
  }, []);

  const handleNewConversation = useCallback(() => {
    clearThreadFromUrl();
    setActiveThreadId(null);
    setActiveRunId(null);
    setSelectedThreadId(null);
    setIsMobileMenuOpen(false);
    setIsChatStreaming(false);
    setChatViewKey((current) => current + 1);
  }, []);

  const handleThreadChanged = useCallback((threadId: string | null) => {
    setActiveThreadId(threadId);

    if (threadId === null) {
      setActiveRunId(null);
    }

    setThreadListVersion((current) => current + 1);
  }, []);

  const handleRunFinalized = useCallback(() => {
    setThreadListVersion((current) => current + 1);
  }, []);

  const handleThreadDeleted = useCallback(
    (threadId: string) => {
      setThreadListVersion((current) => current + 1);

      if (activeThreadId === threadId || selectedThreadId === threadId) {
        handleNewConversation();
      }
    },
    [activeThreadId, handleNewConversation, selectedThreadId],
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

          <AgentOsLogo subtitle="Runtime control plane" />

          <div className="ml-auto hidden items-center gap-3 lg:flex">
            <button
              type="button"
              onClick={() => setIsRuntimeRailOpen((current) => !current)}
              aria-pressed={isRuntimeRailOpen}
              className="agentos-header-action"
            >
              {isRuntimeRailOpen ? "收起检视" : "运行检视"}
            </button>
            <span className="agentos-runtime-tag">Local runtime · Ready</span>
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

          <span className="agentos-runtime-tag ml-auto lg:hidden">Ready</span>
        </div>
      </header>

      <div
        className={`agentos-workspace ${
          isRuntimeRailOpen ? "" : "agentos-workspace-runtime-collapsed"
        }`}
      >
        <aside className="agentos-conversation-rail hidden min-h-0 lg:flex lg:flex-col">
          <ConversationList
            activeThreadId={activeThreadId}
            refreshKey={threadListVersion}
            isChatStreaming={isChatStreaming}
            onNewConversation={handleNewConversation}
            onSelectThread={handleSelectThread}
            onThreadDeleted={handleThreadDeleted}
          />
        </aside>

        <section className="agentos-main-column min-h-0">
          <ChatPanel
            key={chatViewKey}
            selectedThreadId={selectedThreadId}
            onNewConversation={handleNewConversation}
            onRunStarted={setActiveRunId}
            onStreamingChanged={setIsChatStreaming}
            onThreadChanged={handleThreadChanged}
            onRunFinalized={handleRunFinalized}
          />
        </section>

        <aside
          className={`agentos-runtime-rail hidden min-h-0 lg:flex lg:flex-col ${
            isRuntimeRailOpen ? "" : "agentos-runtime-rail-collapsed"
          }`}
        >
          <HealthStatus />
          <RunInspector runId={activeRunId} />

          <section className="agentos-context-card">
            <p className="text-xs font-medium tracking-wide text-zinc-500">运行上下文</p>
            <dl className="mt-4 divide-y divide-zinc-100">
              {runtimeItems.map((item) => (
                <div key={item.label} className="py-3 first:pt-0 last:pb-0">
                  <dt className="text-xs text-zinc-500">{item.label}</dt>
                  <dd className="mt-1 text-sm font-medium">{item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </aside>
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
                refreshKey={threadListVersion}
                isChatStreaming={isChatStreaming}
                onNewConversation={handleNewConversation}
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
