"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { useToast } from "@/components/toast";
import { PROVIDER_KIND_LABELS, boolZh, labelOf } from "@/lib/labels";
import { OpsFetchError, opsJson } from "@/lib/ops-fetch";

type ModelProvider = {
  id: string;
  slug: string;
  name: string;
  kind: string;
  base_url: string;
  default_model: string;
  context_window: number;
  max_output_tokens: number;
  temperature: number | null;
  max_concurrent_runs: number;
  supports_vision: boolean;
  enabled: boolean;
  is_builtin: boolean;
  has_api_key: boolean;
  api_key_preview: string | null;
  created_at: string;
  updated_at: string;
};

type ProviderFormState = {
  slug: string;
  name: string;
  baseUrl: string;
  apiKey: string;
  defaultModel: string;
  contextWindow: string;
  maxOutputTokens: string;
  temperature: string;
  maxConcurrentRuns: string;
  supportsVision: boolean;
  enabled: boolean;
};

const EMPTY_FORM: ProviderFormState = {
  slug: "",
  name: "",
  baseUrl: "",
  apiKey: "",
  defaultModel: "",
  contextWindow: "",
  maxOutputTokens: "",
  temperature: "",
  maxConcurrentRuns: "4",
  supportsVision: false,
  enabled: true,
};

function formFromProvider(provider: ModelProvider): ProviderFormState {
  return {
    slug: provider.slug,
    name: provider.name,
    baseUrl: provider.base_url,
    apiKey: "",
    defaultModel: provider.default_model,
    contextWindow: String(provider.context_window),
    maxOutputTokens: String(provider.max_output_tokens),
    temperature: provider.temperature === null ? "" : String(provider.temperature),
    maxConcurrentRuns: String(provider.max_concurrent_runs),
    supportsVision: provider.supports_vision,
    enabled: provider.enabled,
  };
}

function parsePositiveInt(raw: string, label: string): number {
  const value = Number(raw);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${label}必须是正整数`);
  }
  return value;
}

function parseTemperature(raw: string): number | null {
  if (!raw.trim()) return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0 || value > 2) {
    throw new Error("temperature 必须是 0 到 2 之间的数字");
  }
  return value;
}

export default function ProvidersPage() {
  const router = useRouter();
  const toast = useToast();
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ProviderFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const editing =
    editingId === null ? null : (providers.find((provider) => provider.id === editingId) ?? null);

  const load = useCallback(async () => {
    try {
      const body = await opsJson<{ providers: ModelProvider[] }>("/api/ops/model-providers");
      setProviders(body.providers);
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
  }, [router]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  function patchForm(patch: Partial<ProviderFormState>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  function startCreate() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormOpen(true);
    setError(null);
  }

  function startEdit(provider: ModelProvider) {
    setEditingId(provider.id);
    setForm(formFromProvider(provider));
    setFormOpen(true);
    setError(null);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
  }

  async function submitForm(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        slug: form.slug.trim(),
        name: form.name.trim(),
        base_url: form.baseUrl.trim(),
        default_model: form.defaultModel.trim(),
        context_window: parsePositiveInt(form.contextWindow, "上下文窗口"),
        max_output_tokens: parsePositiveInt(form.maxOutputTokens, "最大输出 tokens"),
        max_concurrent_runs: parsePositiveInt(form.maxConcurrentRuns, "并发上限"),
        supports_vision: form.supportsVision,
        enabled: form.enabled,
      };
      const temperature = parseTemperature(form.temperature);
      const apiKey = form.apiKey.trim();

      if (editing) {
        payload.temperature = temperature;
        if (apiKey) payload.api_key = apiKey;
        await opsJson(`/api/ops/model-providers/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast.show("Provider 已保存");
      } else {
        if (temperature !== null) payload.temperature = temperature;
        if (apiKey) payload.api_key = apiKey;
        await opsJson("/api/ops/model-providers", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        toast.show("Provider 已创建");
      }
      closeForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function clearApiKey() {
    if (!editing) return;
    if (!window.confirm("清除后该 Provider 将不带密钥访问端点。确定清除已保存的 API key？")) {
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await opsJson(`/api/ops/model-providers/${editing.id}`, {
        method: "PATCH",
        body: JSON.stringify({ clear_api_key: true }),
      });
      patchForm({ apiKey: "" });
      await load();
      toast.show("API key 已清除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "清除失败");
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(provider: ModelProvider) {
    setBusyId(provider.id);
    setError(null);
    try {
      await opsJson(`/api/ops/model-providers/${provider.id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !provider.enabled }),
      });
      await load();
      toast.show(provider.enabled ? "已禁用" : "已启用");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  async function removeProvider(provider: ModelProvider) {
    if (confirmId !== provider.id) {
      setConfirmId(provider.id);
      return;
    }
    setBusyId(provider.id);
    setError(null);
    try {
      await opsJson(`/api/ops/model-providers/${provider.id}`, { method: "DELETE" });
      setProviders((prev) => prev.filter((row) => row.id !== provider.id));
      setConfirmId(null);
      if (editingId === provider.id) closeForm();
      toast.show("Provider 已删除");
    } catch (err) {
      if (err instanceof OpsFetchError && err.status === 409) {
        setConfirmId(null);
        toast.show("仍被智能体版本引用，请改用禁用");
      } else {
        setError(err instanceof Error ? err.message : "删除失败");
      }
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader
        title="模型 Provider"
        lead="本地 Ollama 为内置（env 管理）；远程 OpenAI 兼容端点在这里登记后，智能体发版时可选。"
        actions={
          <button type="button" onClick={startCreate} disabled={formOpen}>
            新建 Provider
          </button>
        }
      />
      {error ? <p className="error">{error}</p> : null}

      {formOpen ? (
        <form className="panel stack" onSubmit={(event) => void submitForm(event)}>
          <h2 className="section-title">{editing ? `编辑：${editing.name}` : "新建 Provider"}</h2>
          <div className="form-grid cols-2">
            <label>
              标识（slug）
              <input
                value={form.slug}
                required
                pattern="[a-z0-9][a-z0-9-]*"
                title="小写字母、数字或连字符，且以字母或数字开头"
                placeholder="deepseek"
                spellCheck={false}
                disabled={editing !== null}
                onChange={(event) => patchForm({ slug: event.target.value })}
              />
            </label>
            <label>
              名称
              <input
                value={form.name}
                required
                placeholder="DeepSeek"
                onChange={(event) => patchForm({ name: event.target.value })}
              />
            </label>
          </div>
          <label>
            Base URL（OpenAI 兼容端点）
            <input
              type="url"
              value={form.baseUrl}
              required
              placeholder="https://api.deepseek.com/v1"
              spellCheck={false}
              onChange={(event) => patchForm({ baseUrl: event.target.value })}
            />
          </label>
          <div className="form-grid cols-2">
            <label>
              默认模型
              <input
                value={form.defaultModel}
                required
                placeholder="deepseek-chat"
                spellCheck={false}
                onChange={(event) => patchForm({ defaultModel: event.target.value })}
              />
            </label>
            <label>
              API Key{editing ? "（留空 = 保持不变）" : "（可选）"}
              <input
                type="password"
                value={form.apiKey}
                autoComplete="new-password"
                placeholder={editing ? (editing.api_key_preview ?? "未设置") : "sk-..."}
                spellCheck={false}
                onChange={(event) => patchForm({ apiKey: event.target.value })}
              />
              {editing?.has_api_key ? (
                <span className="field-hint">
                  当前已设置（{editing.api_key_preview}）。
                  <button
                    type="button"
                    className="danger-link"
                    disabled={saving}
                    onClick={() => void clearApiKey()}
                  >
                    清除 key
                  </button>
                </span>
              ) : null}
            </label>
          </div>
          <div className="form-grid cols-2">
            <label>
              上下文窗口（tokens）
              <input
                type="number"
                min={1}
                step={1}
                value={form.contextWindow}
                required
                placeholder="131072"
                onChange={(event) => patchForm({ contextWindow: event.target.value })}
              />
            </label>
            <label>
              最大输出 tokens
              <input
                type="number"
                min={1}
                step={1}
                value={form.maxOutputTokens}
                required
                placeholder="8192"
                onChange={(event) => patchForm({ maxOutputTokens: event.target.value })}
              />
            </label>
          </div>
          <div className="form-grid cols-2">
            <label>
              temperature（可选，0–2）
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={form.temperature}
                placeholder="留空则用端点默认"
                onChange={(event) => patchForm({ temperature: event.target.value })}
              />
            </label>
            <label>
              并发上限
              <input
                type="number"
                min={1}
                step={1}
                value={form.maxConcurrentRuns}
                required
                onChange={(event) => patchForm({ maxConcurrentRuns: event.target.value })}
              />
            </label>
          </div>
          <div className="filter-row">
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.supportsVision}
                onChange={(event) => patchForm({ supportsVision: event.target.checked })}
              />
              支持图片输入 {boolZh(form.supportsVision)}
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => patchForm({ enabled: event.target.checked })}
              />
              启用 {boolZh(form.enabled)}
            </label>
          </div>
          <div className="btn-row">
            <button type="submit" disabled={saving}>
              {saving ? "保存中…" : editing ? "保存修改" : "创建"}
            </button>
            <button type="button" className="ghost" onClick={closeForm}>
              取消
            </button>
          </div>
        </form>
      ) : null}

      {loading ? <Skeleton /> : null}

      {!loading && providers.length === 0 ? <div className="empty">还没有 Provider。</div> : null}

      {!loading && providers.length > 0 ? (
        <div className="row-list">
          {providers.map((provider) => (
            <article key={provider.id} className="row row--ops">
              <div>
                <div className="row__title">
                  {provider.name}
                  {provider.is_builtin ? <span className="pill">内置（env 管理）</span> : null}
                </div>
                <div className="row__meta">
                  <span>{provider.slug}</span>
                  <span>{labelOf(PROVIDER_KIND_LABELS, provider.kind)}</span>
                  <span>{provider.base_url}</span>
                  <span>模型 {provider.default_model}</span>
                  <span>上下文 {provider.context_window}</span>
                  <span>视觉 {boolZh(provider.supports_vision)}</span>
                  <span>Key {provider.api_key_preview ?? "未设置"}</span>
                </div>
              </div>
              <span className={`badge badge--${provider.enabled ? "active" : "disabled"}`}>
                {provider.enabled ? "启用中" : "已禁用"}
              </span>
              {provider.is_builtin ? null : (
                <div className="btn-row">
                  <button
                    type="button"
                    className="ghost"
                    disabled={busyId === provider.id}
                    onClick={() => startEdit(provider)}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busyId === provider.id}
                    onClick={() => void toggleEnabled(provider)}
                  >
                    {provider.enabled ? "禁用" : "启用"}
                  </button>
                  <button
                    type="button"
                    className="danger-link"
                    disabled={busyId === provider.id}
                    onClick={() => void removeProvider(provider)}
                    onBlur={() => {
                      if (confirmId === provider.id) setConfirmId(null);
                    }}
                  >
                    {confirmId === provider.id ? "确认删除" : "删除"}
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}
