"use client";

import { useEffect, useState } from "react";

import { ToolIcon } from "./tool-icons";
import { toolDisplayName, toolProgressLabel } from "./tool-labels";

export type ToolCallStatus = "running" | "done" | "error" | "awaiting_approval";

export type ToolCallState = {
  id: string;
  toolName: string;
  argsText: string;
  status: ToolCallStatus;
  resultSummary?: string;
  provider?: string;
  startedAt?: number;
  durationMs?: number;
  expanded: boolean;
  afterMessageId: string;
};

function stringField(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (typeof value !== "string") {
    return null;
  }

  const trimmed = value.trim();
  return trimmed || null;
}

function numberField(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseArgsRecord(argsText: string): Record<string, unknown> | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Args may still be a partial JSON fragment while streaming.
  }

  return null;
}

function queryFromArgsText(argsText: string): string | null {
  const record = parseArgsRecord(argsText);
  return record ? stringField(record, "query") : null;
}

function urlFromArgsText(argsText: string): string | null {
  const record = parseArgsRecord(argsText);
  return record ? stringField(record, "url") : null;
}

function shortUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    const display = `${parsed.hostname}${path}`;
    return display.length > 56 ? `${display.slice(0, 55)}…` : display;
  } catch {
    return url.length > 56 ? `${url.slice(0, 55)}…` : url;
  }
}

function truncate(text: string, max = 56): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= max) {
    return normalized;
  }
  return `${normalized.slice(0, max - 1)}…`;
}

function firstShortStringField(record: Record<string, unknown>): string | null {
  for (const value of Object.values(record)) {
    if (typeof value === "string" && value.trim()) {
      return truncate(value.trim(), 40);
    }
  }
  return null;
}

/** Key parameter snippet for the collapsed row (toolName stays separate). */
export function toolCallKeyParam(toolCall: ToolCallState): string | null {
  const record = parseArgsRecord(toolCall.argsText);
  if (!record) {
    return toolCall.argsText.trim() ? "…" : null;
  }

  const name = toolCall.toolName;

  if (name === "web_search" || name === "knowledge_search") {
    const query = stringField(record, "query");
    return query ? truncate(query) : null;
  }

  if (name === "fetch_url") {
    const url = stringField(record, "url");
    return url ? shortUrl(url) : null;
  }

  if (name === "read_artifact") {
    const id = stringField(record, "artifact_id");
    return id ? truncate(id, 36) : null;
  }

  if (name === "calculate") {
    const expression = stringField(record, "expression");
    return expression ? truncate(expression) : null;
  }

  if (name === "time_diff") {
    const start = stringField(record, "start");
    if (!start) {
      return null;
    }
    const end = stringField(record, "end");
    return truncate(`${start} → ${end ?? "now"}`);
  }

  if (name === "growth_assess") {
    const parts: string[] = [];
    const sex = stringField(record, "sex");
    if (sex) {
      parts.push(sex);
    }
    const age = numberField(record, "age_months");
    if (age !== null) {
      parts.push(`${age}mo`);
    }
    const height = numberField(record, "height_cm");
    if (height !== null) {
      parts.push(`${height}cm`);
    }
    const weight = numberField(record, "weight_kg");
    if (weight !== null) {
      parts.push(`${weight}kg`);
    }
    return parts.length > 0 ? truncate(parts.join(" · ")) : null;
  }

  if (name.startsWith("case_")) {
    return firstShortStringField(record);
  }

  const query = stringField(record, "query");
  if (query) {
    return truncate(query);
  }
  const url = stringField(record, "url");
  if (url) {
    return shortUrl(url);
  }
  return firstShortStringField(record);
}

export function toolCallStatusLabel(status: ToolCallStatus): string {
  if (status === "running") {
    return "进行中…";
  }
  if (status === "awaiting_approval") {
    return "等待确认";
  }
  if (status === "error") {
    return "失败";
  }
  return "已完成";
}

export function toolCallDurationLabel(durationMs: number | undefined): string | null {
  if (durationMs === undefined || !Number.isFinite(durationMs) || durationMs < 0) {
    return null;
  }
  return durationMs < 1000 ? `${Math.round(durationMs)}ms` : `${(durationMs / 1000).toFixed(1)}s`;
}

/** @deprecated Prefer toolCallKeyParam + toolName; kept for any external imports. */
export function toolCallHeadline(toolCall: ToolCallState): string {
  const param = toolCallKeyParam(toolCall);
  const name = toolDisplayName(toolCall.toolName);
  return param ? `${name}  ${param}` : name;
}

export function ToolCallCard({
  toolCall,
  onToggle,
}: {
  toolCall: ToolCallState;
  onToggle: () => void;
}) {
  const query = queryFromArgsText(toolCall.argsText);
  const url = urlFromArgsText(toolCall.argsText);
  const expression = stringField(parseArgsRecord(toolCall.argsText) ?? {}, "expression");
  const keyParam = toolCallKeyParam(toolCall);
  const statusLabel =
    toolCall.status === "awaiting_approval"
      ? toolCallStatusLabel(toolCall.status)
      : toolProgressLabel(toolCall.toolName, toolCall.status);
  const [now, setNow] = useState<number | null>(null);
  const running = toolCall.status === "running";

  useEffect(() => {
    if (!running || toolCall.durationMs !== undefined || toolCall.startedAt === undefined) {
      return;
    }
    const update = () => setNow(Date.now());
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [running, toolCall.durationMs, toolCall.startedAt]);

  const durationLabel = toolCallDurationLabel(
    toolCall.durationMs ??
      (toolCall.startedAt === undefined || now === null ? undefined : now - toolCall.startedAt),
  );
  const displayName = toolDisplayName(toolCall.toolName);
  const title = keyParam ? `${displayName} ${keyParam}` : displayName;

  return (
    <section
      className={`agentos-tool-call ${
        toolCall.status === "running" ? "agentos-tool-call-running" : ""
      } ${toolCall.status === "error" ? "agentos-tool-call-error" : ""}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={toolCall.expanded}
        className="agentos-tool-call-toggle"
        title={title}
      >
        <span className="agentos-tool-call-main">
          <span className="agentos-tool-call-icon" aria-hidden="true">
            <ToolIcon toolName={toolCall.toolName} />
          </span>
          <span className="agentos-tool-call-text">
            <span className="agentos-tool-call-headline">
              <span className="agentos-tool-call-name">{displayName}</span>
              {keyParam ? <span className="agentos-tool-call-param">{keyParam}</span> : null}
            </span>
            <span className="agentos-tool-call-status">
              {statusLabel}
              {durationLabel ? ` · ${durationLabel}` : null}
            </span>
          </span>
        </span>
        <span className="agentos-tool-call-state" aria-hidden="true">
          {toolCall.expanded ? "▾" : "▸"}
        </span>
      </button>

      {toolCall.expanded ? (
        <div className="agentos-tool-call-content">
          {query ? (
            <p>
              <span className="agentos-tool-call-label">查询内容</span>
              {query}
            </p>
          ) : null}
          {url ? (
            <p>
              <span className="agentos-tool-call-label">网页</span>
              {url}
            </p>
          ) : null}
          {expression ? (
            <p>
              <span className="agentos-tool-call-label">计算内容</span>
              {expression}
            </p>
          ) : null}
          {toolCall.argsText.trim() && !query && !url && !expression ? (
            <pre>{toolCall.argsText.trim()}</pre>
          ) : null}
          {toolCall.provider ? (
            <p>
              <span className="agentos-tool-call-label">来源</span>
              {toolCall.provider}
            </p>
          ) : null}
          {toolCall.resultSummary ? (
            <p>
              <span className="agentos-tool-call-label">
                {toolCall.status === "error" ? "提示" : "结果"}
              </span>
              {toolCall.resultSummary}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function summarizeToolResultContent(content: string): {
  summary: string;
  provider?: string;
  status: ToolCallStatus;
} {
  const trimmed = content.trim();

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      const provider = stringField(record, "provider") ?? undefined;

      if (typeof record.error === "string" && record.error.trim()) {
        return {
          summary: record.error.slice(0, 500),
          provider,
          status: "error",
        };
      }

      if (typeof record.status === "string" && record.status === "approval_required") {
        return {
          summary: "需要审批后才能执行",
          provider,
          status: "awaiting_approval",
        };
      }

      // fetch_url success shape
      if (typeof record.url === "string" && typeof record.text === "string") {
        const title = stringField(record, "title") ?? record.url;
        const truncated = record.truncated === true;
        const totalChars =
          typeof record.total_chars === "number" ? record.total_chars : record.text.length;
        const flag = truncated ? "truncated" : "full";
        return {
          summary: `${provider ?? "fetch_url"}: ${title} (${flag}, ${totalChars} chars)`.slice(
            0,
            500,
          ),
          provider,
          status: "done",
        };
      }

      if (Array.isArray(record.results)) {
        const titles = record.results
          .slice(0, 3)
          .map((item) => {
            if (typeof item !== "object" || item === null) {
              return null;
            }

            const result = item as Record<string, unknown>;
            return stringField(result, "title") ?? stringField(result, "url");
          })
          .filter((title): title is string => title !== null);

        const count = record.results.length;
        const head = `${provider ?? "search"}: ${count} results`;
        const summary = titles.length > 0 ? `${head}; ${titles.join("; ")}` : head;
        return {
          summary: summary.slice(0, 500),
          provider,
          status: "done",
        };
      }

      // Parsed JSON without a structured error field is treated as success.
      if (provider) {
        return {
          summary: trimmed.slice(0, 500),
          provider,
          status: "done",
        };
      }
    }
  } catch {
    // Fall through to a truncated raw summary.
  }

  return {
    summary: trimmed.slice(0, 500),
    // Do not substring-match "error" in page bodies — READMEs often contain that word.
    status: "done",
  };
}
