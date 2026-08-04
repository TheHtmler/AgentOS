"use client";

export type ToolCallStatus = "running" | "done" | "error" | "awaiting_approval";

export type ToolCallState = {
  id: string;
  toolName: string;
  argsText: string;
  status: ToolCallStatus;
  resultSummary?: string;
  provider?: string;
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

function queryFromArgsText(argsText: string): string | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return stringField(parsed as Record<string, unknown>, "query");
    }
  } catch {
    // Args may still be a partial JSON fragment while streaming.
  }

  return null;
}

function urlFromArgsText(argsText: string): string | null {
  const trimmed = argsText.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return stringField(parsed as Record<string, unknown>, "url");
    }
  } catch {
    // Args may still be a partial JSON fragment while streaming.
  }

  return null;
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

function truncate(text: string, max = 48): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= max) {
    return normalized;
  }
  return `${normalized.slice(0, max - 1)}…`;
}

function toolIcon(toolName: string): string {
  if (toolName === "web_search") {
    return "🌐";
  }
  if (toolName === "fetch_url") {
    return "📄";
  }
  return "⚙";
}

/**
 * Single-line Codex-style status: 正在… / 已… (+ short target).
 * Keep verb phrases tool-specific so the row reads as an action log, not a card title.
 */
export function toolCallHeadline(toolCall: ToolCallState): string {
  const query = queryFromArgsText(toolCall.argsText);
  const url = urlFromArgsText(toolCall.argsText);

  if (toolCall.toolName === "web_search") {
    if (toolCall.status === "running") {
      return query ? `正在搜索网页：${truncate(query)}` : "正在搜索网页…";
    }
    if (toolCall.status === "awaiting_approval") {
      return query ? `等待审批搜索：${truncate(query)}` : "等待审批搜索…";
    }
    if (toolCall.status === "error") {
      return query ? `搜索网页失败：${truncate(query)}` : "搜索网页失败";
    }
    return query ? `已搜索网页：${truncate(query)}` : "已完成搜索";
  }

  if (toolCall.toolName === "fetch_url") {
    const target = url ? shortUrl(url) : null;
    if (toolCall.status === "running") {
      return target ? `正在读取：${target}` : "正在读取网页…";
    }
    if (toolCall.status === "awaiting_approval") {
      return target ? `等待审批读取：${target}` : "等待审批读取…";
    }
    if (toolCall.status === "error") {
      return target ? `读取失败：${target}` : "读取网页失败";
    }
    return target ? `已读取：${target}` : "已完成读取";
  }

  // Future tools (file/edit/shell) can plug verb maps here.
  if (toolCall.status === "running") {
    return `正在调用 ${toolCall.toolName}…`;
  }
  if (toolCall.status === "awaiting_approval") {
    return `等待审批 ${toolCall.toolName}…`;
  }
  if (toolCall.status === "error") {
    return `${toolCall.toolName} 失败`;
  }
  return `已完成 ${toolCall.toolName}`;
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
  const headline = toolCallHeadline(toolCall);

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
        title={headline}
      >
        <span className="agentos-tool-call-title">
          <span aria-hidden="true" className="agentos-tool-call-icon">
            {toolIcon(toolCall.toolName)}
          </span>
          <span className="agentos-tool-call-headline">{headline}</span>
        </span>
        <span className="agentos-tool-call-state" aria-hidden="true">
          {toolCall.expanded ? "▾" : "▸"}
        </span>
      </button>

      {toolCall.expanded ? (
        <div className="agentos-tool-call-content">
          {query ? (
            <p>
              <span className="agentos-tool-call-label">query</span>
              {query}
            </p>
          ) : null}
          {url ? (
            <p>
              <span className="agentos-tool-call-label">url</span>
              {url}
            </p>
          ) : null}
          {toolCall.argsText.trim() && !query && !url ? (
            <pre>{toolCall.argsText.trim()}</pre>
          ) : null}
          {toolCall.provider ? (
            <p>
              <span className="agentos-tool-call-label">provider</span>
              {toolCall.provider}
            </p>
          ) : null}
          {toolCall.resultSummary ? (
            <p>
              <span className="agentos-tool-call-label">
                {toolCall.status === "error" ? "error" : "result"}
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
