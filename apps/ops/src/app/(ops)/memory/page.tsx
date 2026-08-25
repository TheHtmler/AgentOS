"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { formatTime } from "@/lib/format";
import { MEMORY_KIND_LABELS, MEMORY_STATUS_LABELS, labelOf } from "@/lib/labels";
import { OpsFetchError, opsJson } from "@/lib/ops-fetch";

type OpsMemory = {
  id: string;
  user_id: string;
  user_email: string;
  agent_id: string;
  agent_name: string;
  case_id: string | null;
  kind: string;
  key: string | null;
  content: string;
  tags: string[];
  status: string;
  source_thread_id: string | null;
  source_run_id: string | null;
  created_at: string;
  updated_at: string;
};

type OpsAgent = {
  id: string;
  slug: string;
  name: string;
};

const KIND_FILTERS = ["all", "profile", "note"] as const;
const STATUS_FILTERS = ["active", "archived"] as const;
const ALL_AGENTS = "all";

function summarize(content: string, max = 120): string {
  const collapsed = content.replace(/\s+/g, " ").trim();
  return collapsed.length > max ? `${collapsed.slice(0, max)}…` : collapsed;
}

export default function MemoryPage() {
  const router = useRouter();
  const toast = useToast();
  const [memories, setMemories] = useState<OpsMemory[]>([]);
  const [agents, setAgents] = useState<OpsAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [emailDraft, setEmailDraft] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [agentFilter, setAgentFilter] = useState(ALL_AGENTS);
  const [kindFilter, setKindFilter] = useState<(typeof KIND_FILTERS)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>("active");
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const loadMemories = useCallback(async () => {
    const params = new URLSearchParams();
    if (userEmail.trim()) params.set("user_email", userEmail.trim());
    if (agentFilter !== ALL_AGENTS) params.set("agent_id", agentFilter);
    if (kindFilter !== "all") params.set("kind", kindFilter);
    params.set("status", statusFilter);
    try {
      const body = await opsJson<{ memories: OpsMemory[] }>(
        `/api/ops/memories?${params.toString()}`,
      );
      setMemories(body.memories);
      setError(null);
    } catch (err) {
      if (err instanceof OpsFetchError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [agentFilter, kindFilter, router, statusFilter, userEmail]);

  useEffect(() => {
    void (async () => {
      await loadMemories();
    })();
  }, [loadMemories]);

  useEffect(() => {
    void (async () => {
      try {
        const body = await opsJson<{ agents: OpsAgent[] }>("/api/ops/agents");
        setAgents(body.agents);
      } catch {
        // The agent filter simply stays at "all" when the list cannot be loaded.
      }
    })();
  }, []);

  async function removeMemory(memoryId: string) {
    if (confirmId !== memoryId) {
      setConfirmId(memoryId);
      return;
    }
    setSavingId(memoryId);
    setError(null);
    try {
      await opsJson(`/api/ops/memories/${memoryId}`, { method: "DELETE" });
      setMemories((prev) => prev.filter((row) => row.id !== memoryId));
      setConfirmId(null);
      toast.show("记忆已删除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader title="记忆" lead="查看并清理用户长期记忆；删除立即生效，不影响会话历史。" />

      <p className="hint">
        <strong>档案</strong> 结构化槽位（如身高），每次对话必注入 · <strong>笔记</strong>{" "}
        自由文本，按关键词和向量召回 · <strong>已归档</strong> 不再注入
      </p>

      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setUserEmail(emailDraft);
        }}
      >
        <select
          className="user-select"
          aria-label="按智能体筛选"
          value={agentFilter}
          onChange={(event) => setAgentFilter(event.target.value)}
        >
          <option value={ALL_AGENTS}>全部智能体</option>
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </select>
        <input
          className="search-input"
          value={emailDraft}
          placeholder="按用户邮箱筛选"
          onChange={(event) => setEmailDraft(event.target.value)}
        />
        <button type="submit" className="secondary">
          筛选
        </button>
      </form>

      <div className="toolbar">
        <div className="seg" role="tablist" aria-label="记忆类型">
          {KIND_FILTERS.map((item) => (
            <button
              key={item}
              type="button"
              className={kindFilter === item ? "is-selected" : ""}
              onClick={() => setKindFilter(item)}
            >
              {item === "all" ? "全部类型" : MEMORY_KIND_LABELS[item]}
            </button>
          ))}
        </div>
        <div className="seg" role="tablist" aria-label="记忆状态">
          {STATUS_FILTERS.map((item) => (
            <button
              key={item}
              type="button"
              className={statusFilter === item ? "is-selected" : ""}
              onClick={() => setStatusFilter(item)}
            >
              {MEMORY_STATUS_LABELS[item]}
            </button>
          ))}
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <Skeleton /> : <p className="muted">{memories.length} 条记忆</p>}

      {!loading && memories.length > 0 ? (
        <div className="row-list">
          {memories.map((memory) => (
            <article key={memory.id} className="row">
              <div className="row__main">
                <div className="row__title">{memory.key ?? summarize(memory.content)}</div>
                <div className="row__meta">
                  <span
                    className={`badge badge--${memory.status === "active" ? "active" : "disabled"}`}
                  >
                    {labelOf(MEMORY_STATUS_LABELS, memory.status)}
                  </span>
                  <span className="badge badge--unknown">
                    {labelOf(MEMORY_KIND_LABELS, memory.kind)}
                  </span>
                  <span>{memory.user_email}</span>
                  <span>{memory.agent_name}</span>
                  {memory.key ? <span>{summarize(memory.content, 60)}</span> : null}
                  {memory.tags.map((tag) => (
                    <span key={tag}>#{tag}</span>
                  ))}
                </div>
              </div>
              <span className="muted">{formatTime(memory.updated_at)}</span>
              <button
                type="button"
                className="danger-link"
                disabled={savingId === memory.id}
                onClick={() => void removeMemory(memory.id)}
                onBlur={() => {
                  if (confirmId === memory.id) setConfirmId(null);
                }}
              >
                {confirmId === memory.id ? "确认删除" : "删除"}
              </button>
            </article>
          ))}
        </div>
      ) : null}

      {!loading && memories.length === 0 ? (
        <div className="empty">没有匹配的记忆。可以调整筛选条件。</div>
      ) : null}
    </div>
  );
}
