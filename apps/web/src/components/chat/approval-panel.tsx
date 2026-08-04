"use client";

import { useState } from "react";

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

export function ApprovalPanel({ runId, interrupts, onResolved, onError }: ApprovalPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [denyReason, setDenyReason] = useState("");

  async function submit(decision: "approve" | "deny") {
    if (submitting || interrupts.length === 0) {
      return;
    }

    setSubmitting(true);
    try {
      const response = await fetch(`/api/runs/${runId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          decisions: interrupts.map((item) => ({
            tool_call_id: item.tool_call_id,
            decision,
            message: decision === "deny" ? denyReason.trim() || null : null,
          })),
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
    <section className="agentos-approval-panel" aria-label="工具审批">
      <header className="agentos-approval-panel-header">
        <p className="agentos-approval-panel-title">需要你的批准才能继续</p>
        <p className="agentos-approval-panel-subtitle">
          批准后将执行下列工具并继续生成；拒绝后模型会收到说明并可改口。
        </p>
      </header>

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

      <div className="agentos-approval-actions">
        <button
          type="button"
          className="agentos-approval-deny"
          disabled={submitting}
          onClick={() => void submit("deny")}
        >
          {submitting ? "提交中…" : "拒绝"}
        </button>
        <button
          type="button"
          className="agentos-approval-approve"
          disabled={submitting}
          onClick={() => void submit("approve")}
        >
          {submitting ? "提交中…" : "批准"}
        </button>
      </div>
    </section>
  );
}
