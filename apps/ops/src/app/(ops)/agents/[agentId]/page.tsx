"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { formatTime } from "@/lib/format";
import { AGENT_KIND_LABELS, AGENT_STATUS_LABELS, boolZh, labelOf } from "@/lib/labels";
import { opsJson } from "@/lib/ops-fetch";

type OpsAgentVersion = {
  id: string;
  version: number;
  system_prompt_overlay: string;
  tool_policy_overrides: Record<string, string> | null;
  memory_enabled: boolean;
  case_enabled: boolean;
  is_published: boolean;
  created_at: string;
};

type OpsAgentDetail = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  kind: string;
  status: string;
  is_default: boolean;
  published_version: OpsAgentVersion | null;
  versions: OpsAgentVersion[];
};

export default function AgentDetailPage() {
  const params = useParams<{ agentId: string }>();
  const [agent, setAgent] = useState<OpsAgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [overlay, setOverlay] = useState("");
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [caseEnabled, setCaseEnabled] = useState(false);
  const [policyText, setPolicyText] = useState("");

  const applyVersion = useCallback((version: OpsAgentVersion | null) => {
    setOverlay(version?.system_prompt_overlay ?? "");
    setMemoryEnabled(version?.memory_enabled ?? false);
    setCaseEnabled(version?.case_enabled ?? false);
    setPolicyText(
      version?.tool_policy_overrides ? JSON.stringify(version.tool_policy_overrides, null, 2) : "",
    );
  }, []);

  const load = useCallback(async () => {
    try {
      const detail = await opsJson<OpsAgentDetail>(`/api/ops/agents/${params.agentId}`);
      setAgent(detail);
      applyVersion(detail.published_version);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [applyVersion, params.agentId]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  async function onPublish(event: FormEvent) {
    event.preventDefault();
    if (!window.confirm("发布后立即作用于新的用户对话。确定发布这个版本？")) return;
    setSaving(true);
    setError(null);
    try {
      let tool_policy_overrides: Record<string, "allow" | "ask" | "deny"> | null = null;
      if (policyText.trim()) {
        const parsed = JSON.parse(policyText) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error('工具策略必须是 JSON 对象，例如 {"web_search":"ask"}');
        }
        tool_policy_overrides = parsed as Record<string, "allow" | "ask" | "deny">;
      }
      const updated = await opsJson<OpsAgentDetail>(`/api/ops/agents/${params.agentId}/versions`, {
        method: "POST",
        body: JSON.stringify({
          system_prompt_overlay: overlay,
          memory_enabled: memoryEnabled,
          case_enabled: caseEnabled,
          tool_policy_overrides,
        }),
      });
      setAgent(updated);
      applyVersion(updated.published_version);
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError("工具策略 JSON 格式无效");
      } else {
        setError(err instanceof Error ? err.message : "发布失败");
      }
    } finally {
      setSaving(false);
    }
  }

  if (!agent && !error) {
    return <p className="muted">加载中…</p>;
  }

  return (
    <div className="stack">
      <div>
        <Link href="/agents" className="muted">
          ← 返回智能体
        </Link>
        <h1 className="page-title">{agent?.name ?? "智能体"}</h1>
        <p className="muted">
          标识：{agent?.slug} · {labelOf(AGENT_KIND_LABELS, agent?.kind)} ·{" "}
          {labelOf(AGENT_STATUS_LABELS, agent?.status)}
          {agent?.is_default ? " · 默认" : ""}
        </p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {agent ? (
        <>
          <form className="panel stack" onSubmit={(event) => void onPublish(event)}>
            <h2 className="section-title">发布新版本</h2>
            <p className="muted" style={{ margin: 0 }}>
              版本不可原地修改。保存会新建一版并设为当前发布版，旧版保留可回看。
            </p>
            <label>
              系统提示叠加
              <textarea
                rows={14}
                value={overlay}
                spellCheck={false}
                onChange={(event) => setOverlay(event.target.value)}
              />
            </label>
            <div className="filter-row">
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  onChange={(event) => setMemoryEnabled(event.target.checked)}
                />
                长期记忆 {boolZh(memoryEnabled)}
              </label>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={caseEnabled}
                  onChange={(event) => setCaseEnabled(event.target.checked)}
                />
                档案能力 {boolZh(caseEnabled)}
              </label>
            </div>
            <label>
              工具策略覆盖（可选 JSON）
              <textarea
                rows={5}
                value={policyText}
                spellCheck={false}
                placeholder='{"web_search":"ask"}'
                onChange={(event) => setPolicyText(event.target.value)}
              />
            </label>
            <button type="submit" disabled={saving}>
              {saving ? "发布中…" : "发布新版本"}
            </button>
          </form>

          <section className="panel stack">
            <h2 className="section-title">版本历史</h2>
            {agent.versions.length === 0 ? <p className="muted">暂无版本</p> : null}
            {agent.versions.map((version) => (
              <article key={version.id} className="doc-card">
                <div className="doc-card__title">
                  v{version.version}
                  {version.is_published ? <span className="pill">当前发布</span> : null}
                </div>
                <div className="doc-card__meta">
                  <span>记忆 {boolZh(version.memory_enabled)}</span>
                  <span>档案 {boolZh(version.case_enabled)}</span>
                  <span>{formatTime(version.created_at)}</span>
                </div>
                <button type="button" className="secondary" onClick={() => applyVersion(version)}>
                  载入到编辑区
                </button>
              </article>
            ))}
          </section>
        </>
      ) : null}
    </div>
  );
}
