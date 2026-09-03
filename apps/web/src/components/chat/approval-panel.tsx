"use client";

import { useMemo, useState } from "react";
import { Check, ShieldAlert, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { toolDisplayName } from "./tool-labels";

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
      const label = typeof row.label === "string" && row.label.trim() ? row.label.trim() : key;
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
            if (decision === "approve" && item.tool_name === "case_slot_collect" && isCollectForm) {
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
            : `确认提交失败（${response.status}）`;
        onError(detail);
        return;
      }

      onResolved();
    } catch {
      onError("暂时无法提交确认，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      className="border-primary/40 bg-card/90 shadow-lg backdrop-blur-sm"
      aria-label={isCollectForm ? "补充资料" : "确认操作"}
    >
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <span className="flex size-6 items-center justify-center rounded-full bg-primary/15 text-primary">
            <ShieldAlert aria-hidden="true" className="size-3.5" />
          </span>
          {isCollectForm ? "需要补充资料" : "需要你的确认才能继续"}
        </CardTitle>
        <CardDescription>
          {isCollectForm
            ? "填写后会保存到当前资料并继续回答；取消后助手会改用已有信息或说明缺口。"
            : "确认后助手会继续处理；拒绝后助手会根据你的说明调整回答。"}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3 pb-3">
        {isCollectForm ? (
          <ul className="grid gap-2.5">
            {collectFields.map((field) => (
              <li key={field.key} className="grid gap-1.5">
                <Label htmlFor={`collect-${field.key}`} className="text-xs font-medium">
                  {field.label}
                  {field.unit ? `（${field.unit}）` : ""}
                </Label>
                <Input
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
                {field.reason ? (
                  <p className="text-xs text-muted-foreground">{field.reason}</p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <ul className="grid gap-1.5">
            {interrupts.map((item) => (
              <li
                key={item.id}
                className="flex flex-col gap-0.5 rounded-lg border border-border bg-muted/30 px-3 py-2"
              >
                <span className="flex items-center gap-2">
                  <Badge variant="secondary" className="font-medium">
                    {toolDisplayName(item.tool_name)}
                  </Badge>
                  <span className="truncate text-xs text-muted-foreground">
                    {argPreview(item.tool_args)}
                  </span>
                </span>
                <span className="text-[0.65rem] text-muted-foreground">
                  过期：{new Date(item.expires_at).toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <CardFooter className="flex-col items-stretch gap-2.5 border-t border-border pt-3">
        {!isCollectForm ? (
          <div className="grid gap-1.5">
            <Label htmlFor="deny-reason" className="text-xs font-medium">
              拒绝理由（可选）
            </Label>
            <Input
              id="deny-reason"
              type="text"
              value={denyReason}
              onChange={(event) => setDenyReason(event.target.value)}
              disabled={submitting}
              placeholder="例如：不要访问该链接"
              maxLength={500}
            />
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={submitting}
            onClick={() => void submit("deny")}
          >
            <X aria-hidden="true" className="size-3.5" />
            {submitting ? "提交中…" : isCollectForm ? "取消" : "拒绝"}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={submitting}
            onClick={() => void submit("approve")}
          >
            <Check aria-hidden="true" className="size-3.5" />
            {submitting ? "提交中…" : isCollectForm ? "提交并继续" : "确认"}
          </Button>
        </div>
      </CardFooter>
    </Card>
  );
}
