"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

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
  const [agents, setAgents] = useState<OpsAgent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const body = await opsJson<{ agents: OpsAgent[] }>("/api/ops/agents");
      setAgents(body.agents);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
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
        <h1 className="page-title">Agents</h1>
        <p className="muted">启停、描述与默认 Agent（不改 version 配置）</p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      <div className="doc-list always">
        {agents.map((agent) => (
          <article key={agent.id} className="doc-card">
            <div className="doc-card__title">
              {agent.name}
              {agent.is_default ? <span className="pill">default</span> : null}
            </div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {agent.slug} · {agent.kind} · {agent.status}
            </div>
            <div className="doc-card__meta">
              <span>memory {agent.memory_enabled == null ? "—" : String(agent.memory_enabled)}</span>
              <span>case {agent.case_enabled == null ? "—" : String(agent.case_enabled)}</span>
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
                <button
                  type="button"
                  className="secondary block"
                  disabled={busyId === agent.id}
                  onClick={() => startEdit(agent)}
                >
                  编辑
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
