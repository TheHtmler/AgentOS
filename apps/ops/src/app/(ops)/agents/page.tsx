"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { AGENT_KIND_LABELS, AGENT_STATUS_LABELS, boolZh, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type OpsAgent = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  kind: string;
  status: string;
  is_default: boolean;
  memory_enabled: boolean | null;
  case_enabled: boolean | null;
};

export default function AgentsPage() {
  const toast = useToast();
  const [agents, setAgents] = useState<OpsAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await opsJson<{ agents: OpsAgent[] }>("/api/ops/agents");
      setAgents(body.agents);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  function startEdit(agent: OpsAgent) {
    setEditingId(agent.id);
    setName(agent.name);
    setDescription(agent.description ?? "");
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editingId) return;
    setBusyId(editingId);
    setError(null);
    try {
      await opsJson(`/api/ops/agents/${editingId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, description }),
      });
      setEditingId(null);
      await load();
      toast.show("名称已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusyId(null);
    }
  }

  async function patchAgent(id: string, body: Record<string, unknown>, done: string) {
    setBusyId(id);
    setError(null);
    try {
      await opsJson(`/api/ops/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await load();
      toast.show(done);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader title="智能体" lead="启停、设默认，或进入详情发布提示词版本。" />
      {error ? <p className="error">{error}</p> : null}
      {loading ? <Skeleton /> : null}

      {!loading && agents.length === 0 ? <div className="empty">还没有智能体。</div> : null}

      <div className="row-list">
        {agents.map((agent) => (
          <article key={agent.id} className="row row--ops">
            <div>
              <Link href={`/agents/${agent.id}`} className="row__title linkish">
                {agent.name}
                {agent.is_default ? <span className="pill">默认</span> : null}
              </Link>
              <div className="row__meta">
                <span>{agent.slug}</span>
                <span>{labelOf(AGENT_KIND_LABELS, agent.kind)}</span>
                <span>记忆 {boolZh(agent.memory_enabled)}</span>
                <span>档案 {boolZh(agent.case_enabled)}</span>
              </div>
              <p className="muted" style={{ margin: "6px 0 0" }}>
                {agent.description || "无描述"}
              </p>
            </div>
            <span className={`badge badge--${agent.status}`}>
              {labelOf(AGENT_STATUS_LABELS, agent.status)}
            </span>
            {editingId === agent.id ? (
              <form className="stack" onSubmit={(event) => void saveEdit(event)}>
                <label>
                  名称
                  <input value={name} onChange={(e) => setName(e.target.value)} required />
                </label>
                <label>
                  描述
                  <input value={description} onChange={(e) => setDescription(e.target.value)} />
                </label>
                <div className="btn-row">
                  <button type="submit" disabled={busyId === agent.id}>
                    保存
                  </button>
                  <button type="button" className="ghost" onClick={() => setEditingId(null)}>
                    取消
                  </button>
                </div>
              </form>
            ) : (
              <div className="btn-row">
                <Link href={`/agents/${agent.id}`} className="linkish">
                  发版
                </Link>
                <button
                  type="button"
                  className="ghost"
                  disabled={busyId === agent.id}
                  onClick={() => startEdit(agent)}
                >
                  改名
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busyId === agent.id}
                  onClick={() =>
                    void patchAgent(
                      agent.id,
                      { status: agent.status === "active" ? "disabled" : "active" },
                      agent.status === "active" ? "已禁用" : "已启用",
                    )
                  }
                >
                  {agent.status === "active" ? "禁用" : "启用"}
                </button>
                {!agent.is_default ? (
                  <button
                    type="button"
                    disabled={busyId === agent.id || agent.status !== "active"}
                    onClick={() => void patchAgent(agent.id, { is_default: true }, "已设为默认")}
                  >
                    设为默认
                  </button>
                ) : null}
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
