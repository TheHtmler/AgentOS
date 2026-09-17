"use client";

import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useAgUiInterrupts } from "@assistant-ui/react-ag-ui";
import { Check, ShieldAlert, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAgentOsInterruptPayloads } from "@/lib/agentos-interrupt-payloads";

type CollectField = { key: string; label: string; unit?: string; reason?: string };

function parseCollectFields(args: Record<string, unknown>): CollectField[] {
  const raw = args.fields_json;
  if (typeof raw !== "string" || !raw.trim()) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((item) => {
      if (typeof item !== "object" || item === null) return [];
      const row = item as Record<string, unknown>;
      const key = typeof row.key === "string" ? row.key.trim() : "";
      if (!key) return [];
      return [
        {
          key,
          label: typeof row.label === "string" && row.label.trim() ? row.label.trim() : key,
          unit: typeof row.unit === "string" && row.unit.trim() ? row.unit.trim() : undefined,
          reason:
            typeof row.reason === "string" && row.reason.trim() ? row.reason.trim() : undefined,
        },
      ];
    });
  } catch {
    return [];
  }
}

/** 病例资料收集保留领域表单，但决议通过 assistant-ui interrupt 生命周期提交。 */
export function ApprovalPanel({
  toolCallId,
  toolArgs,
  respondToApproval,
}: {
  toolCallId: string;
  toolArgs: Record<string, unknown>;
  respondToApproval: ToolCallMessagePartProps["respondToApproval"];
}) {
  const interrupts = useAgUiInterrupts();
  const { setInterruptPayload } = useAgentOsInterruptPayloads();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const fields = useMemo(() => parseCollectFields(toolArgs), [toolArgs]);
  const current = interrupts.find((item) => item.toolCallId === toolCallId);

  if (current === undefined) return null;
  const currentInterrupt = current;

  async function submit(approved: boolean) {
    if (submitting) return;
    if (approved) {
      const missing = fields.filter((field) => !(values[field.key] ?? "").trim());
      if (missing.length > 0) {
        setError(`请填写：${missing.map((field) => field.label).join("、")}`);
        return;
      }
    }

    const filled = Object.fromEntries(
      fields.map((field) => [field.key, (values[field.key] ?? "").trim()]),
    );
    setSubmitting(true);
    setError(null);
    try {
      setInterruptPayload(
        currentInterrupt.id,
        approved ? { override_args: { values: filled } } : null,
      );
      await respondToApproval?.({ approved });
    } catch (submitError) {
      setInterruptPayload(currentInterrupt.id, null);
      setError(submitError instanceof Error ? submitError.message : "确认提交失败，请稍后重试。");
      setSubmitting(false);
    }
  }

  return (
    <Card className="border-primary/40 bg-card/90 shadow-sm" aria-label="补充资料">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <span className="flex size-6 items-center justify-center rounded-full bg-primary/15 text-primary">
            <ShieldAlert aria-hidden="true" className="size-3.5" />
          </span>
          需要补充资料
        </CardTitle>
        <CardDescription>
          填写后会保存到当前资料并继续回答；取消后助手会说明现有信息缺口。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pb-3">
        <ul className="grid gap-2.5">
          {fields.map((field) => (
            <li key={field.key} className="grid gap-1.5">
              <Label htmlFor={`collect-${toolCallId}-${field.key}`} className="text-xs font-medium">
                {field.label}
                {field.unit ? `（${field.unit}）` : ""}
              </Label>
              <Input
                id={`collect-${toolCallId}-${field.key}`}
                value={values[field.key] ?? ""}
                onChange={(event) =>
                  setValues((currentValues) => ({
                    ...currentValues,
                    [field.key]: event.target.value,
                  }))
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
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </CardContent>
      <CardFooter className="justify-end gap-2 border-t border-border pt-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={submitting}
          onClick={() => void submit(false)}
        >
          <X aria-hidden="true" className="size-3.5" />
          取消
        </Button>
        <Button type="button" size="sm" disabled={submitting} onClick={() => void submit(true)}>
          <Check aria-hidden="true" className="size-3.5" />
          {submitting ? "提交中…" : "提交并继续"}
        </Button>
      </CardFooter>
    </Card>
  );
}
