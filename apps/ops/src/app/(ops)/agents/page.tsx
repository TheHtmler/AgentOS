"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { opsJson } from "@/lib/ops-fetch";
import { AGENT_KIND_LABELS, AGENT_STATUS_LABELS, boolZh, labelOf } from "@/lib/labels";

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
  const [agents, setAgents] = useState<OpsAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusyId(null);
    }
  }

  async function patchAgent(id: string, body: Record<string, unknown>) {
    setBusyId(id);
    setError(null);
    try {
      await opsJson(`/api/ops/agents/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      <div>
        <h1 className="page-title">智能体</h1>
        <p className="muted page-lead">启停、描述、默认智能体，以及发布提示词版本</p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      <div className="doc-list always">
        {agents.map((agent) => (
          <article key={agent.id} className="doc-card">
            <Link href={`/agents/${agent.id}`} className="doc-card__title linkish">
              {agent.name}
              {agent.is_default ? <span className="pill">默认</span> : null}
            </Link>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              标识：{agent.slug} · {labelOf(AGENT_KIND_LABELS, agent.kind)} ·{" "}
              {labelOf(AGENT_STATUS_LABELS, agent.status)}
            </div>
            <div className="doc-card__meta">
              <span>记忆 {boolZh(agent.memory_enabled)}</span>
              <span>档案 {boolZh(agent.case_enabled)}</span>
            </div>
            <p style={{ margin: 0 }}>{agent.description || "无描述"}</p>

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
                <button type="submit" disabled={busyId === agent.id}>
                  保存
                </button>
                <button type="button" className="secondary" onClick={() => setEditingId(null)}>
                  取消
                </button>
              </form>
            ) : (
              <div className="doc-card__actions">
                <Link href={`/agents/${agent.id}`} className="linkish">
                  版本与提示词
                </Link>
                <button
                  type="button"
                  className="secondary block"
                  disabled={busyId === agent.id}
                  onClick={() => startEdit(agent)}
                >
                  编辑名称
                </button>
                <button
                  type="button"
                  className="secondary block"
                  disabled={busyId === agent.id}
                  onClick={() =>
                    void patchAgent(agent.id, {
                      status: agent.status === "active" ? "disabled" : "active",
                    })
                  }
                >
                  {agent.status === "active" ? "禁用" : "启用"}
                </button>
                {!agent.is_default ? (
                  <button
                    type="button"
                    className="block"
                    disabled={busyId === agent.id || agent.status !== "active"}
                    onClick={() => void patchAgent(agent.id, { is_default: true })}
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
