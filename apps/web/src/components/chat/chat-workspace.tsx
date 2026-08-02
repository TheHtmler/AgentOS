"use client";

import { useCallback, useState } from "react";

import { ChatPanel } from "@/components/chat/chat-panel";
import { ConversationList } from "@/components/chat/conversation-list";
import { RunInspector } from "@/components/run/run-inspector";
import { HealthStatus } from "@/components/system/health-status";

const runtimeItems = [
  { label: "执行入口", value: "FastAPI Agent API" },
  { label: "模型路由", value: "Ollama · agentos-gemma4:8k" },
  { label: "会话存储", value: "PostgreSQL Thread" },
];

export function ChatWorkspace() {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadListVersion, setThreadListVersion] = useState(0);

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

  return (
    <div className="mx-auto grid max-w-7xl gap-5 px-5 py-8 lg:grid-cols-[15rem_minmax(0,1fr)_20rem]">
      <aside className="order-2 lg:order-1">
        <ConversationList activeThreadId={activeThreadId} refreshKey={threadListVersion} />
      </aside>

      <section className="order-1 min-w-0 lg:order-2">
        <ChatPanel
          onRunStarted={setActiveRunId}
          onThreadChanged={handleThreadChanged}
          onRunFinalized={handleRunFinalized}
        />
      </section>

      <aside className="order-3 space-y-5">
        <HealthStatus />
        <RunInspector runId={activeRunId} />

        <section className="border border-zinc-200 bg-white p-5">
          <p className="text-sm font-medium text-zinc-500">当前配置</p>
          <dl className="mt-4 divide-y divide-zinc-100">
            {runtimeItems.map((item) => (
              <div key={item.label} className="py-3 first:pt-0 last:pb-0">
                <dt className="text-xs text-zinc-500">{item.label}</dt>
                <dd className="mt-1 text-sm font-medium text-zinc-800">{item.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      </aside>
    </div>
  );
}
