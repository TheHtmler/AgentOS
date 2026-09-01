"use client";

import { useEffect, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { ToolIcon } from "./tool-icons";
import { toolDisplayName, toolProgressLabel } from "./tool-labels";

// Shared edge-drag resizing for the right-side preview panes (sandbox files and
// upload attachments). Width is clamped to the CSS min/max of the pane.
function usePaneWidth() {
  const [width, setWidth] = useState<number | null>(null);

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const pane = event.currentTarget.closest(".agentos-file-preview-pane");
    const startWidth = pane instanceof HTMLElement ? pane.getBoundingClientRect().width : 416;
    const startX = event.clientX;
    const onMove = (moveEvent: PointerEvent) => {
      const next = Math.round(startWidth + startX - moveEvent.clientX);
      setWidth(Math.min(720, Math.max(288, next)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return { width, startResize };
}

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
  files?: SandboxFile[];
};

export type SandboxFile = {
  path: string;
  size: number;
  mimeType: string;
};

function sandboxFileUrl(file: SandboxFile, download = false): string {
  const params = new URLSearchParams({ path: file.path });
  if (download) {
    params.set("download", "1");
  }
  return `/api/sandbox/files?${params.toString()}`;
}

export function sandboxFilesFromValue(value: unknown): SandboxFile[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.flatMap((item) => {
    if (typeof item !== "object" || item === null) {
      return [];
    }
    const record = item as Record<string, unknown>;
    if (
      typeof record.path !== "string" ||
      !record.path.trim() ||
      typeof record.size !== "number" ||
      !Number.isFinite(record.size) ||
      record.size < 0 ||
      typeof record.mime_type !== "string" ||
      !record.mime_type.trim()
    ) {
      return [];
    }
    return [
      {
        path: record.path,
        size: record.size,
        mimeType: record.mime_type,
      },
    ];
  });
}

function formatFileSize(size: number): string {
  if (size < 1_024) {
    return `${size} B`;
  }
  if (size < 1_024 * 1_024) {
    return `${(size / 1_024).toFixed(1)} KB`;
  }
  return `${(size / (1_024 * 1_024)).toFixed(1)} MB`;
}

function isTextFile(file: SandboxFile): boolean {
  return (
    file.mimeType.startsWith("text/") ||
    ["application/json", "application/javascript", "application/xml"].includes(file.mimeType)
  );
}

export function GeneratedFileList({
  files,
  selectedPath,
  onSelect,
}: {
  files: SandboxFile[];
  selectedPath?: string | null;
  onSelect?: (file: SandboxFile) => void;
}) {
  if (files.length === 0) {
    return null;
  }

  return (
    <div className="agentos-generated-files">
      <span className="agentos-generated-files-label">生成文件</span>
      <div className="agentos-generated-files-list">
        {files.map((file) => (
          <button
            key={file.path}
            type="button"
            className={`agentos-generated-file-row ${
              selectedPath === file.path ? "agentos-generated-file-row-selected" : ""
            }`}
            aria-pressed={selectedPath === file.path}
            title="在右侧预览文件"
            onClick={() => onSelect?.(file)}
          >
            <span className="agentos-generated-file-main">
              <span className="agentos-generated-file-name">{file.path}</span>
              <span className="agentos-generated-file-meta">
                {file.mimeType} · {formatFileSize(file.size)}
              </span>
            </span>
            <span className="agentos-generated-file-action" aria-hidden="true">
              ›
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function SandboxTextPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(url, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("文件暂时无法读取");
        }
        setText(await response.text());
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "文件暂时无法读取");
        }
      });
    return () => controller.abort();
  }, [url]);

  return <pre>{error ?? text ?? "正在读取…"}</pre>;
}

export function SandboxFilePreviewPane({
  file,
  onClose,
}: {
  file: SandboxFile;
  onClose: () => void;
}) {
  const previewUrl = sandboxFileUrl(file);
  const textFile = isTextFile(file);
  const { width, startResize } = usePaneWidth();

  return (
    <aside
      className="agentos-file-preview-pane"
      aria-label="文件预览"
      style={width === null ? undefined : { width }}
    >
      <div className="agentos-file-preview-resize" onPointerDown={startResize} aria-hidden="true" />
      <header className="agentos-file-preview-header">
        <div className="agentos-file-preview-heading">
          <strong title={file.path}>{file.path}</strong>
          <span>
            {file.mimeType} · {formatFileSize(file.size)}
          </span>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭文件预览" title="关闭预览">
          ×
        </button>
      </header>
      <div className="agentos-file-preview-body">
        {textFile ? (
          <SandboxTextPreview key={`${file.path}:${file.size}`} url={previewUrl} />
        ) : file.mimeType.startsWith("image/") ? (
          // The endpoint checks the session and owner before serving this URL.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={previewUrl} alt={file.path} />
        ) : file.mimeType === "application/pdf" ? (
          <iframe src={previewUrl} title={file.path} sandbox="" />
        ) : (
          <p className="agentos-file-preview-unavailable">此文件类型暂不支持在线预览。</p>
        )}
      </div>
      <footer className="agentos-file-preview-footer">
        <a href={sandboxFileUrl(file, true)}>下载文件</a>
      </footer>
    </aside>
  );
}

type UploadPreviewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "text"; text: string }
  | { kind: "binary"; objectUrl: string; mimeType: string };

// History user messages carry only artifact_id lines, so the MIME type is
// discovered from the content response headers before picking a renderer.
export function UploadPreviewPane({
  artifactId,
  onClose,
}: {
  artifactId: string;
  onClose: () => void;
}) {
  const url = `/api/uploads/${artifactId}/content`;
  const [state, setState] = useState<UploadPreviewState>({ kind: "loading" });
  const { width, startResize } = usePaneWidth();

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void fetch(url, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("附件暂时无法读取");
        }
        const mimeType = (response.headers.get("content-type") ?? "")
          .split(";")[0]
          .trim()
          .toLowerCase();
        if (
          mimeType.startsWith("text/") ||
          mimeType === "application/json" ||
          mimeType === "application/xml"
        ) {
          setState({ kind: "text", text: await response.text() });
          return;
        }
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        setState({
          kind: "binary",
          objectUrl,
          mimeType: mimeType || "application/octet-stream",
        });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setState({
            kind: "error",
            message: reason instanceof Error ? reason.message : "附件暂时无法读取",
          });
        }
      });
    return () => {
      controller.abort();
      if (objectUrl !== null) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [url]);

  return (
    <aside
      className="agentos-file-preview-pane"
      aria-label="附件预览"
      style={width === null ? undefined : { width }}
    >
      <div className="agentos-file-preview-resize" onPointerDown={startResize} aria-hidden="true" />
      <header className="agentos-file-preview-header">
        <div className="agentos-file-preview-heading">
          <strong>用户附件</strong>
          <span>{artifactId.slice(0, 8)}…</span>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭附件预览" title="关闭预览">
          ×
        </button>
      </header>
      <div className="agentos-file-preview-body">
        {state.kind === "loading" ? (
          <pre>正在读取…</pre>
        ) : state.kind === "error" ? (
          <p className="agentos-file-preview-unavailable">{state.message}</p>
        ) : state.kind === "text" ? (
          <pre>{state.text}</pre>
        ) : state.mimeType.startsWith("image/") ? (
          // The endpoint checks the session and owner before serving these bytes.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={state.objectUrl} alt="用户附件" />
        ) : state.mimeType === "application/pdf" ? (
          <iframe src={state.objectUrl} title="用户附件 PDF" sandbox="" />
        ) : (
          <p className="agentos-file-preview-unavailable">此文件类型暂不支持在线预览。</p>
        )}
      </div>
      <footer className="agentos-file-preview-footer">
        <a href={url} download>
          下载附件
        </a>
      </footer>
    </aside>
  );
}

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
  onFileSelect,
  selectedFilePath,
}: {
  toolCall: ToolCallState;
  onToggle: () => void;
  onFileSelect?: (file: SandboxFile) => void;
  selectedFilePath?: string | null;
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
  // read_artifact success shape: {title, text, offset, truncated, total_chars, next_offset}.
  // Live streams carry the full record, so the fetched window can render as readable
  // text instead of a raw-JSON summary; history replays only have the short summary.
  const artifactText =
    toolCall.toolName === "read_artifact" ? stringField(toolCall.resultData ?? {}, "text") : null;
  const artifactTitle =
    toolCall.toolName === "read_artifact" ? stringField(toolCall.resultData ?? {}, "title") : null;
  const artifactTotalChars =
    toolCall.toolName === "read_artifact"
      ? numberField(toolCall.resultData ?? {}, "total_chars")
      : null;
  const artifactTruncated =
    toolCall.toolName === "read_artifact" && toolCall.resultData?.truncated === true;
  const files = toolCall.files ?? sandboxFilesFromValue(toolCall.resultData?.files);

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
  const summary =
    toolCall.status === "error" && toolCall.resultSummary
      ? truncate(toolCall.resultSummary, 96)
      : (keyParam ?? (toolCall.resultSummary ? truncate(toolCall.resultSummary, 96) : null));

  return (
    <section
      className={`agentos-tool-call ${
        toolCall.status === "running" ? "agentos-tool-call-running" : ""
      } ${toolCall.status === "error" ? "agentos-tool-call-error" : ""}`}
      data-status={toolCall.status}
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
              {summary ? <span className="agentos-tool-call-param">{summary}</span> : null}
            </span>
            <span className="agentos-tool-call-status">
              {statusLabel}
              {durationLabel ? ` · ${durationLabel}` : null}
            </span>
          </span>
        </span>
        <span className="agentos-tool-call-state" aria-hidden="true">
          {toolCall.expanded ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </span>
      </button>

      {files.length > 0 ? (
        <GeneratedFileList files={files} selectedPath={selectedFilePath} onSelect={onFileSelect} />
      ) : null}

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
          {artifactText !== null ? (
            <div className="agentos-tool-call-artifact">
              <span className="agentos-tool-call-label">正文</span>
              <span className="agentos-tool-call-artifact-meta">
                {artifactTitle ?? "附件"} · 共 {artifactTotalChars ?? "?"} 字符
                {artifactTruncated ? " · 本段为截取片段" : ""}
              </span>
              <pre>{artifactText}</pre>
            </div>
          ) : null}
          {toolCall.resultSummary && artifactText === null ? (
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

      if (Array.isArray(record.files) && typeof record.ok === "boolean") {
        const files = sandboxFilesFromValue(record.files);
        return {
          summary: `sandbox_exec: ${record.ok ? "执行完成" : "执行失败"}${
            files.length > 0 ? `，生成 ${files.length} 个文件` : ""
          }`,
          status: record.ok ? "done" : "error",
          resultData: record,
          provider,
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
