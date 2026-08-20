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
  // Parsed result JSON, only available on the live stream (full TOOL_CALL_RESULT
  // content); history replays carry just the ≤500-char resultSummary.
  resultData?: Record<string, unknown>;
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

type KnowledgeHit = {
  title: string;
  snippet: string | null;
  sourceUrl: string | null;
  sourceLabel: string | null;
};

// knowledge_search hit shape: {title, content, document_title, section_label,
// source_url, source_label, ...} (see tools/knowledge/tool.py payload).
function knowledgeHitsFromResult(data: Record<string, unknown> | undefined): KnowledgeHit[] {
  if (data === undefined || !Array.isArray(data.results)) {
    return [];
  }

  const hits: KnowledgeHit[] = [];
  for (const item of data.results.slice(0, 5)) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const title = stringField(record, "title") ?? stringField(record, "document_title");
    if (title === null) {
      continue;
    }
    const content = stringField(record, "content");
    hits.push({
      title,
      snippet: content === null ? null : truncate(content, 140),
      sourceUrl: stringField(record, "source_url"),
      sourceLabel: stringField(record, "source_label"),
    });
  }
  return hits;
}

type SearchLink = { title: string; url: string };

// web_search hit shape: {title, url, ...}.
function searchLinksFromResult(data: Record<string, unknown> | undefined): SearchLink[] {
  if (data === undefined || !Array.isArray(data.results)) {
    return [];
  }

  const links: SearchLink[] = [];
  for (const item of data.results.slice(0, 5)) {
    if (typeof item !== "object" || item === null) {
      continue;
    }
    const record = item as Record<string, unknown>;
    const url = stringField(record, "url");
    if (url === null) {
      continue;
    }
    links.push({ title: stringField(record, "title") ?? url, url });
  }
  return links;
}

const DEDICATED_ARG_KEYS = new Set(["query", "url", "expression"]);

function formatArgValue(value: unknown): string {
  if (typeof value === "string") {
    return truncate(value, 200);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value === null || value === undefined) {
    return "—";
  }
  return truncate(JSON.stringify(value), 200);
}

// Generic key→value rows replace the old raw-JSON dump for tools without a
// dedicated arg row (query/url/expression keep their labelled rows).
function extraArgEntries(argsText: string): [string, string][] {
  const record = parseArgsRecord(argsText);
  if (record === null) {
    return [];
  }
  return Object.entries(record)
    .filter(([key]) => !DEDICATED_ARG_KEYS.has(key))
    .map(([key, value]) => [key, formatArgValue(value)]);
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
  const extraArgs = extraArgEntries(toolCall.argsText);
  const keyParam = toolCallKeyParam(toolCall);
  const statusLabel =
    toolCall.status === "awaiting_approval"
      ? toolCallStatusLabel(toolCall.status)
      : toolProgressLabel(toolCall.toolName, toolCall.status);
  const [now, setNow] = useState<number | null>(null);
  const running = toolCall.status === "running";

  const knowledgeHits =
    toolCall.toolName === "knowledge_search" ? knowledgeHitsFromResult(toolCall.resultData) : [];
  const searchLinks =
    toolCall.toolName === "web_search" ? searchLinksFromResult(toolCall.resultData) : [];
  const fetchLinkUrl =
    toolCall.toolName === "fetch_url" ? stringField(toolCall.resultData ?? {}, "url") : null;
  const fetchLinkTitle =
    toolCall.toolName === "fetch_url" ? stringField(toolCall.resultData ?? {}, "title") : null;

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
          {url && fetchLinkUrl === null ? (
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
          {extraArgs.map(([key, value]) => (
            <p key={key}>
              <span className="agentos-tool-call-label">{key}</span>
              {value}
            </p>
          ))}
          {toolCall.argsText.trim() && !query && !url && !expression && extraArgs.length === 0 ? (
            <pre>{toolCall.argsText.trim()}</pre>
          ) : null}
          {fetchLinkUrl !== null ? (
            <p>
              <span className="agentos-tool-call-label">链接</span>
              <a href={fetchLinkUrl} target="_blank" rel="noreferrer">
                {fetchLinkTitle ?? fetchLinkUrl}
              </a>
            </p>
          ) : null}
          {knowledgeHits.length > 0 ? (
            <div className="agentos-tool-call-hits">
              <span className="agentos-tool-call-label">命中片段</span>
              <ul>
                {knowledgeHits.map((hit, index) => (
                  <li key={`${hit.title}-${index}`}>
                    <p className="agentos-tool-call-hit-title">{hit.title}</p>
                    {hit.snippet ? (
                      <p className="agentos-tool-call-hit-snippet">{hit.snippet}</p>
                    ) : null}
                    {hit.sourceUrl ? (
                      <a href={hit.sourceUrl} target="_blank" rel="noreferrer">
                        {hit.sourceLabel ?? hit.sourceUrl}
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {searchLinks.length > 0 ? (
            <div className="agentos-tool-call-hits">
              <span className="agentos-tool-call-label">搜索结果</span>
              <ul>
                {searchLinks.map((link, index) => (
                  <li key={`${link.url}-${index}`}>
                    <a href={link.url} target="_blank" rel="noreferrer">
                      {link.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
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
  resultData?: Record<string, unknown>;
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
          resultData: record,
        };
      }

      if (typeof record.status === "string" && record.status === "approval_required") {
        return {
          summary: "需要审批后才能执行",
          provider,
          status: "awaiting_approval",
          resultData: record,
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
          resultData: record,
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
          resultData: record,
        };
      }

      // Parsed JSON without a structured error field is treated as success.
      if (provider) {
        return {
          summary: trimmed.slice(0, 500),
          provider,
          status: "done",
          resultData: record,
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
