"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { useToast } from "@/components/toast";
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
  knowledge_base_slugs: string[] | null;
  model_provider_id: string;
  memory_recall_top_k: number | null;
  memory_recall_max_chars: number | null;
  history_max_runs: number | null;
  agent_max_requests_per_run: number | null;
  is_published: boolean;
  created_at: string;
};

type ModelProviderOption = {
  id: string;
  name: string;
  default_model: string;
  enabled: boolean;
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

type KnowledgeBaseOption = {
  slug: string;
  name: string;
  status: string;
};

type ToolAction = "inherit" | "allow" | "ask" | "deny";

type OpsTool = {
  name: string;
  domain: string;
  risk: string;
  default_action: "allow" | "ask" | "deny";
  effective_action: "allow" | "ask" | "deny";
  enabled: boolean;
  description: string;
  source: "builtin" | "mcp";
};

type OpsToolsResponse = {
  tools: OpsTool[];
};

const ACTION_LABELS: Record<ToolAction, string> = {
  inherit: "继承平台默认",
  allow: "允许",
  ask: "每次审批",
  deny: "禁止",
};

const RISK_LABELS: Record<string, string> = {
  read: "只读",
  write: "写入",
  exec: "执行",
  external: "外部访问",
};

const TUNING_FIELDS = [
  {
    key: "memory_recall_top_k",
    label: "记忆召回条数",
    min: 1,
    max: 50,
    hint: "每轮对话最多注入多少条长期记忆",
  },
  {
    key: "memory_recall_max_chars",
    label: "记忆召回字符上限",
    min: 200,
    max: 20000,
    hint: "注入记忆的总字符数上限",
  },
  {
    key: "history_max_runs",
    label: "历史窗口 Run 数",
    min: 1,
    max: 20,
    hint: "每轮对话携带最近几轮历史",
  },
  {
    key: "agent_max_requests_per_run",
    label: "单 Run 请求上限",
    min: 1,
    max: 50,
    hint: "一次回答允许的最大模型调用步数，超出自动停止",
  },
] as const;

type TuningKey = (typeof TUNING_FIELDS)[number]["key"];
type TuningDraft = Record<TuningKey, string>;

const EMPTY_TUNING: TuningDraft = {
  memory_recall_top_k: "",
  memory_recall_max_chars: "",
  history_max_runs: "",
  agent_max_requests_per_run: "",
};

function parseTuningValue(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? Math.trunc(value) : null;
}

function tuningSummary(version: OpsAgentVersion): string {
  const parts = TUNING_FIELDS.filter((field) => version[field.key] !== null).map(
    (field) => `${field.label} ${version[field.key]}`,
  );
  return parts.length > 0 ? parts.join(" · ") : "继承默认";
}

export default function AgentDetailPage() {
  const params = useParams<{ agentId: string }>();
  const [agent, setAgent] = useState<OpsAgentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [overlay, setOverlay] = useState("");
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [caseEnabled, setCaseEnabled] = useState(false);
  const [knowledgeBaseSlugs, setKnowledgeBaseSlugs] = useState<string[]>([]);
  const [bases, setBases] = useState<KnowledgeBaseOption[]>([]);
  const [providers, setProviders] = useState<ModelProviderOption[]>([]);
  const [modelProviderId, setModelProviderId] = useState("");
  const [toolSpecs, setToolSpecs] = useState<OpsTool[]>([]);
  const [toolActions, setToolActions] = useState<Record<string, ToolAction>>({});
  const [tuning, setTuning] = useState<TuningDraft>(EMPTY_TUNING);

  const applyVersion = useCallback((version: OpsAgentVersion | null, specs: OpsTool[]) => {
    setOverlay(version?.system_prompt_overlay ?? "");
    setMemoryEnabled(version?.memory_enabled ?? false);
    setCaseEnabled(version?.case_enabled ?? false);
    const overrides = version?.tool_policy_overrides ?? {};
    setToolActions(
      Object.fromEntries(
        specs
          .filter((spec) => spec.source === "builtin")
          .map((spec) => [spec.name, overrides[spec.name] ?? "inherit"]),
      ) as Record<string, ToolAction>,
    );
    setKnowledgeBaseSlugs(version?.knowledge_base_slugs ?? []);
    setModelProviderId(version?.model_provider_id ?? "");
    setTuning(
      Object.fromEntries(
        TUNING_FIELDS.map((field) => [
          field.key,
          version?.[field.key] === null || version?.[field.key] === undefined
            ? ""
            : String(version[field.key]),
        ]),
      ) as TuningDraft,
    );
  }, []);

  const load = useCallback(async () => {
    try {
      const [detail, toolsResponse] = await Promise.all([
        opsJson<OpsAgentDetail>(`/api/ops/agents/${params.agentId}`),
        opsJson<OpsToolsResponse>("/api/ops/tools"),
      ]);
      setAgent(detail);
      setToolSpecs(toolsResponse.tools);
      applyVersion(detail.published_version, toolsResponse.tools);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
    try {
      const body = await opsJson<{ providers: ModelProviderOption[] }>("/api/ops/model-providers");
      setProviders(body.providers);
    } catch {
      setProviders([]);
    }
    try {
      const body = await opsJson<{ bases: KnowledgeBaseOption[] }>("/api/ops/knowledge/bases");
      setBases(body.bases.filter((base) => base.status === "active"));
    } catch {
      setBases([]);
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
      const knownNames = new Set(toolSpecs.map((spec) => spec.name));
      const previousOverrides = agent?.published_version?.tool_policy_overrides ?? {};
      const tool_policy_overrides = Object.fromEntries(
        Object.entries({ ...previousOverrides, ...toolActions }).filter(
          ([name, action]) => !knownNames.has(name) || action !== "inherit",
        ),
      ) as Record<string, "allow" | "ask" | "deny">;
      const knowledge_base_slugs = knowledgeBaseSlugs.length > 0 ? knowledgeBaseSlugs : null;
      const updated = await opsJson<OpsAgentDetail>(`/api/ops/agents/${params.agentId}/versions`, {
        method: "POST",
        body: JSON.stringify({
          system_prompt_overlay: overlay,
          memory_enabled: memoryEnabled,
          case_enabled: caseEnabled,
          tool_policy_overrides: Object.keys(tool_policy_overrides).length
            ? tool_policy_overrides
            : null,
          knowledge_base_slugs,
          model_provider_id: modelProviderId,
          memory_recall_top_k: parseTuningValue(tuning.memory_recall_top_k),
          memory_recall_max_chars: parseTuningValue(tuning.memory_recall_max_chars),
          history_max_runs: parseTuningValue(tuning.history_max_runs),
          agent_max_requests_per_run: parseTuningValue(tuning.agent_max_requests_per_run),
        }),
      });
      setAgent(updated);
      applyVersion(updated.published_version, toolSpecs);
      toast.show("新版本已发布");
    } catch (err) {
      setError(err instanceof Error ? err.message : "发布失败");
    } finally {
      setSaving(false);
    }
  }

  if (!agent && !error) {
    return <p className="muted">加载中…</p>;
  }

  const selectableProviders = providers.filter(
    (provider) => provider.enabled || provider.id === modelProviderId,
  );

  function providerLabel(providerId: string | null): string {
    if (providerId === null) return "本地（默认）";
    return providers.find((provider) => provider.id === providerId)?.name ?? "已删除的 Provider";
  }

  return (
    <div className="stack">
      {toast.node}
      <div>
        <Link href="/agents" className="crumb">
          ← 智能体
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
              本页字段全部可选：留空 /
              不勾选即继承当前默认行为。保存会新建一版并设为当前发布版（立即对新对话生效），旧版保留可回看。
            </p>
            <label>
              系统提示叠加（可选）
              <textarea
                rows={14}
                value={overlay}
                spellCheck={false}
                onChange={(event) => setOverlay(event.target.value)}
              />
              <span className="field-hint">
                追加在内置系统提示之后的指令，用于约束这个 Agent 的回答风格与边界；留空 =
                只使用内置提示。
              </span>
            </label>
            <div className="filter-row">
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={memoryEnabled}
                  onChange={(event) => setMemoryEnabled(event.target.checked)}
                />
                长期记忆 {boolZh(memoryEnabled)}
                <span className="field-hint">
                  自动从对话提取用户偏好/病情要点，后续对话自动携带
                </span>
              </label>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={caseEnabled}
                  onChange={(event) => setCaseEnabled(event.target.checked)}
                />
                档案能力 {boolZh(caseEnabled)}
                <span className="field-hint">启用 Case 档案（结构化随访信息），写入需用户确认</span>
              </label>
            </div>
            <div className="stack">
              <div>
                <h3 className="section-title">内置工具策略</h3>
                <p className="field-hint">
                  配置写入新版本；“继承平台默认”由服务端环境策略决定。Sandbox 执行默认需要审批。
                </p>
              </div>
              <div className="tool-policy-list">
                {toolSpecs
                  .filter((spec) => spec.source === "builtin")
                  .map((spec) => (
                    <div key={spec.name} className="tool-policy-row">
                      <div>
                        <div className="tool-policy-row__title">
                          <code>{spec.name}</code>
                          <span className="pill">{RISK_LABELS[spec.risk] ?? spec.risk}</span>
                          {!spec.enabled ? (
                            <span className="pill pill--danger">平台未启用</span>
                          ) : null}
                        </div>
                        <div className="field-hint">{spec.description}</div>
                      </div>
                      <select
                        value={toolActions[spec.name] ?? "inherit"}
                        onChange={(event) =>
                          setToolActions((current) => ({
                            ...current,
                            [spec.name]: event.target.value as ToolAction,
                          }))
                        }
                      >
                        {(Object.keys(ACTION_LABELS) as ToolAction[]).map((action) => (
                          <option key={action} value={action}>
                            {ACTION_LABELS[action]}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
              </div>
            </div>
            <div>
              <span>知识库范围（可选）</span>
              <div className="filter-row">
                {bases.map((base) => (
                  <label key={base.slug} className="inline-check">
                    <input
                      type="checkbox"
                      checked={knowledgeBaseSlugs.includes(base.slug)}
                      onChange={(event) =>
                        setKnowledgeBaseSlugs((current) =>
                          event.target.checked
                            ? [...current, base.slug]
                            : current.filter((slug) => slug !== base.slug),
                        )
                      }
                    />
                    {base.name}（{base.slug}）
                  </label>
                ))}
              </div>
              <span className="field-hint">
                勾选该 Agent 可检索的知识库；都不勾 = 不限制（可检索全部知识库）。
              </span>
            </div>
            <label>
              <span className="req">*</span>模型 Provider
              <select
                value={modelProviderId}
                onChange={(event) => setModelProviderId(event.target.value)}
              >
                <option value="" disabled>
                  请选择 Provider
                </option>
                {selectableProviders.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name}（{provider.default_model}）
                    {provider.enabled ? "" : "（已禁用）"}
                  </option>
                ))}
              </select>
              <span className="field-hint">
                该 Agent 新对话使用的第三方模型端点；发布级绑定，端点故障不会自动换模型。
              </span>
            </label>
            <div className="stack">
              <div>
                <h3 className="section-title">运行参数</h3>
                <p className="field-hint">全部可选，留空继承环境默认；随新版本固化。</p>
              </div>
              <div className="filter-row">
                {TUNING_FIELDS.map((field) => (
                  <label key={field.key}>
                    {field.label}（{field.min}–{field.max}）
                    <input
                      type="number"
                      min={field.min}
                      max={field.max}
                      value={tuning[field.key]}
                      onChange={(event) =>
                        setTuning((current) => ({
                          ...current,
                          [field.key]: event.target.value,
                        }))
                      }
                    />
                    <span className="field-hint">{field.hint}</span>
                  </label>
                ))}
              </div>
            </div>
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
                  <span>知识库 {version.knowledge_base_slugs?.join(", ") ?? "不限制"}</span>
                  <span>模型 {providerLabel(version.model_provider_id)}</span>
                  <span>运行参数 {tuningSummary(version)}</span>
                  <span>{formatTime(version.created_at)}</span>
                </div>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => applyVersion(version, toolSpecs)}
                >
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
