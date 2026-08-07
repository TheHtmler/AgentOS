"use client";

import { useMemo, useState } from "react";

export type PendingInterrupt = {
  id: string;
  tool_call_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  expires_at: string;
};

type ApprovalPanelProps = {
  runId: string;
  interrupts: PendingInterrupt[];
  onResolved: () => void;
  onError: (message: string) => void;
};

type CollectField = {
  key: string;
  label: string;
  unit?: string;
  reason?: string;
};

function argPreview(args: Record<string, unknown>): string {
  const url = typeof args.url === "string" ? args.url : null;
  const query = typeof args.query === "string" ? args.query : null;
  if (url) {
    return url;
  }
  if (query) {
    return query;
  }
  try {
    return JSON.stringify(args);
  } catch {
    return "";
  }
}

function parseCollectFields(args: Record<string, unknown>): CollectField[] {
  const raw = args.fields_json;
  if (typeof raw !== "string" || !raw.trim()) {
    return [];
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    const fields: CollectField[] = [];
    for (const item of parsed) {
      if (typeof item !== "object" || item === null) {
        continue;
      }
      const row = item as Record<string, unknown>;
      const key = typeof row.key === "string" ? row.key.trim() : "";
      if (!key) {
        continue;
      }
      const label =
        typeof row.label === "string" && row.label.trim() ? row.label.trim() : key;
      const unit = typeof row.unit === "string" && row.unit.trim() ? row.unit.trim() : undefined;
      const reason =
        typeof row.reason === "string" && row.reason.trim() ? row.reason.trim() : undefined;
      fields.push({ key, label, unit, reason });
    }
    return fields;
  } catch {
    return [];
  }
}

export function ApprovalPanel({ runId, interrupts, onResolved, onError }: ApprovalPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [denyReason, setDenyReason] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});

  const collectInterrupt = useMemo(
    () => interrupts.find((item) => item.tool_name === "case_slot_collect") ?? null,
    [interrupts],
  );
  const collectFields = useMemo(
    () => (collectInterrupt ? parseCollectFields(collectInterrupt.tool_args) : []),
    [collectInterrupt],
  );
  const isCollectForm = collectInterrupt !== null && collectFields.length > 0;

  async function submit(decision: "approve" | "deny") {
    if (submitting || interrupts.length === 0) {
      return;
    }

    if (decision === "approve" && isCollectForm) {
      const missing = collectFields.filter((field) => !(values[field.key] ?? "").trim());
      if (missing.length > 0) {
        onError(`请填写：${missing.map((field) => field.label).join("、")}`);
        return;
      }
    }

    setSubmitting(true);
    try {
      const response = await fetch(`/api/runs/${runId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          decisions: interrupts.map((item) => {
            const base = {
              tool_call_id: item.tool_call_id,
              decision,
              message: decision === "deny" ? denyReason.trim() || null : null,
            };
            if (
              decision === "approve" &&
              item.tool_name === "case_slot_collect" &&
              isCollectForm
            ) {
              const filled: Record<string, string> = {};
              for (const field of collectFields) {
                filled[field.key] = (values[field.key] ?? "").trim();
              }
              return {
                ...base,
                override_args: { values: filled },
              };
            }
            return base;
          }),
        }),
      });

      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null);
        const detail =
          typeof payload === "object" &&
          payload !== null &&
          "detail" in payload &&
          typeof (payload as { detail: unknown }).detail === "string"
            ? (payload as { detail: string }).detail
            : `审批提交失败（${response.status}）`;
        onError(detail);
        return;
      }

      onResolved();
    } catch {
      onError("无法连接审批服务，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="agentos-approval-panel"
      aria-label={isCollectForm ? "补充档案信息" : "工具审批"}
    >
      <header className="agentos-approval-panel-header">
        <p className="agentos-approval-panel-title">
          {isCollectForm ? "需要补充档案信息" : "需要你的批准才能继续"}
        </p>
        <p className="agentos-approval-panel-subtitle">
          {isCollectForm
            ? "填写后将写入当前档案并继续回答；取消后模型会改用已有信息或说明缺口。"
            : "批准后将执行下列工具并继续生成；拒绝后模型会收到说明并可改口。"}
        </p>
      </header>

      {isCollectForm ? (
        <ul className="agentos-approval-list">
          {collectFields.map((field) => (
            <li key={field.key} className="agentos-approval-item agentos-approval-field">
              <label className="agentos-approval-field-label" htmlFor={`collect-${field.key}`}>
                <span>
                  {field.label}
                  {field.unit ? `（${field.unit}）` : ""}
                </span>
                {field.reason ? (
                  <span className="agentos-approval-field-reason">{field.reason}</span>
                ) : null}
              </label>
              <input
                id={`collect-${field.key}`}
                type="text"
                inputMode={
                  field.key.endsWith("_cm") ||
                  field.key.endsWith("_kg") ||
                  field.key.endsWith("_months")
                    ? "decimal"
                    : "text"
                }
                value={values[field.key] ?? ""}
                onChange={(event) =>
                  setValues((prev) => ({ ...prev, [field.key]: event.target.value }))
                }
                disabled={submitting}
                placeholder={field.unit ? `例如数值，单位 ${field.unit}` : "请填写"}
                maxLength={200}
              />
            </li>
          ))}
        </ul>
      ) : (
        <ul className="agentos-approval-list">
          {interrupts.map((item) => (
            <li key={item.id} className="agentos-approval-item">
              <span className="agentos-approval-tool">{item.tool_name}</span>
              <span className="agentos-approval-args">{argPreview(item.tool_args)}</span>
              <span className="agentos-approval-expires">
                过期：{new Date(item.expires_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}

      {!isCollectForm ? (
        <label className="agentos-approval-reason">
          <span>拒绝理由（可选）</span>
          <input
            type="text"
            value={denyReason}
            onChange={(event) => setDenyReason(event.target.value)}
            disabled={submitting}
            placeholder="例如：不要访问该链接"
            maxLength={500}
          />
        </label>
      ) : null}

      <div className="agentos-approval-actions">
        <button
          type="button"
          className="agentos-approval-deny"
          disabled={submitting}
          onClick={() => void submit("deny")}
        >
          {submitting ? "提交中…" : isCollectForm ? "取消" : "拒绝"}
        </button>
        <button
          type="button"
          className="agentos-approval-approve"
          disabled={submitting}
          onClick={() => void submit("approve")}
        >
          {submitting ? "提交中…" : isCollectForm ? "提交并继续" : "批准"}
        </button>
      </div>
    </section>
  );
}
